#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : aws.py
# @Author: Bai Yanzheng
# @Date  : 2020/4/12
# @Desc  : 调用AWS EC2服务，启停机、摘挂盘，创建盘
#

import time
import boto3
import config as cfg

client_ec2 = None
ec2 = None


def setup():
    global client_ec2, ec2
    client_ec2 = boto3.client('ec2')
    ec2 = boto3.resource('ec2')


#
def get_images():
    filters = [{'Name': 'state', 'Values': ['available']}] + cfg.args.filters
    images = ec2.images.filter(Filters=filters)
    return images


# 把AMI目标机启动为虚机，以获取其磁盘
def image2instance(image_id):
    instances = ec2.create_instances(ImageId=image_id, InstanceType='t3.micro', MaxCount=1, MinCount=1,
                                     Placement={'AvailabilityZone': cfg.instance['availabilityZone']})
    instance = instances[0]
    instance.create_tags(Tags=[dict(Key='Name', Value='tmp4shrinking'), dict(Key='Project', Value='ShrinkVolume')])
    instance.wait_until_running()
    return instance


# 获取本机所在AZ、指定tag的实例列表
def get_instances(same_az=True):
    f = [{'Name': 'instance-state-name', 'Values': ['pending', 'running', 'shutting-down', 'stopping', 'stopped']}]
    if same_az:
        f.append({'Name': 'availability-zone', 'Values': [cfg.instance['availabilityZone'], ]})
    f += cfg.args.filters
    instances = ec2.instances.filter(Filters=f)
    return instances


#  获取指定实例、需缩容的磁盘列表
def get_instance_volumes(instance):
    """
    :param instance: aws 实例对象
    :return:
    [
        dict(origin=volume, dup=None,
            instance=instance, dev=volume.attachments[0]['Device'], state=instance.state['Name'])        {
    ]
    """
    volumes = []
    for volume in instance.volumes.all():
        if volume.size < cfg.MinVolumeSize:  # 太小的盘缩容意义不大
            continue
        if 'a' in volume.attachments[0]['Device']:  # 系统盘
            if 'b' in cfg.args.omit and 'a' in volume.attachments[0]['Device']:
                continue
        if volume.volume_type in ['sc1', 'st1'] and volume.size < 500:  # 最小为500G，再缩没有意义
            continue
        volumes.append(dict(origin=volume,
                            instance=instance, dev=volume.attachments[0]['Device'], state=instance.state['Name']))
    return volumes


# 从虚机摘除多个EBS盘
def detach_volumes(volumes, tags=None):
    tags = [] if not tags else tags
    for v in volumes:
        upwt = '%s,%sGib,%s,IOPS:%s' % \
               (v['instance'].id, v['origin'].size, v['origin'].volume_type, v['origin'].iops)
        v['origin'].create_tags(Tags=tags + [dict(Key='UPWT', Value=upwt)])
        v['origin'].detach_from_instance()
        cfg.log.info(' %s detached from the %s %s(%s) for shrinking...' %
                     (v['origin'].id, v['state'], v['instance'].id, v['dev']))
    time.sleep(3)


# 停掉运行的虚机
def stop_instance(instance, interactive=False):
    state = instance.state['Name']
    if interactive and state in ['pending', 'running']:
        if input('%s is %s, Stop it for shrinking? <Y>  ' % (instance.id, state)) not in cfg.YES:
            print('shrink for %s is skipped...\n' % instance.id)
            return False
    instance.stop()
    # 等待完成停机
    try:
        instance.wait_until_stopped()
    except Exception:
        pass
    if instance.state['Name'] != 'stopped':
        cfg.log.warning('Unable to stop %s, Ignoring...' % instance.id)
        return False
    return True


# 创建EBS盘, 其他可变参数可以直接传递进来
def create_volume(VolumeType='gp2', **argv):
    parameters = {k: v for k, v in argv.items() if v}
    size = parameters.get('Size', 0)

    if size and VolumeType in ['sc1', 'st1']:
        size = min(size, 500)
        parameters['Size'] = size
    parameters['VolumeType'] = VolumeType
    parameters['AvailabilityZone'] = cfg.instance['availabilityZone']

    volume = ec2.create_volume(**parameters)

    for i in range(cfg.TIMEOUT):
        volume.load()
        if volume.state == 'available':
            break
        time.sleep(1)
    else:
        raise UserWarning('Time out')
    cfg.log.info('%s Created. %s' % (volume.id, parameters))
    return volume


# 从EC2拆离EBS盘
def detach__volume(volume):
    volume.load()
    if volume.state == 'in-use':
        volume.detach_from_instance()  # 先尝试卸下
    for i in range(cfg.TIMEOUT):
        volume.load()
        if volume.state == 'available':
            return True
        time.sleep(1)
    else:
        return False


# 磁盘挂到EC2
def attach_volume(volume, device, instance_id):
    detach__volume(volume)  # 先尝试卸下
    volume.attach_to_instance(Device=device, InstanceId=instance_id)
    for i in range(cfg.TIMEOUT):
        volume.load()
        if volume.attachments and volume.attachments[0]['State'] == 'attached':
            break
        time.sleep(1)
    else:
        raise UserWarning('attach %s Time out' % volume.id)
    msg = 'shrinking' if instance_id == cfg.instance['instanceId'] else 'original'
    cfg.log.info('%s attached to the %s server: %s%s.' % (volume.id, msg, instance_id, device))
    time.sleep(3)


# 完成1块盘缩容后的收尾工作 EC2上的新盘或原盘挂回，按需启动
def wind_up(idx_repository):
    volume = cfg.volumeRepository[idx_repository]
    if not volume.get('result', ''):  # 本磁盘没有完成，恢复
        volume['result'] = volume['origin']
    instance = volume.get('instance', '')
    if not instance:  # 快照缩容，删掉原临时volume
        if volume['result'] == volume['dup']:  # 快照缩容成功
            cfg.log.info('[SUCCESS]%s for %s(Snapshot)' % (volume['dup'].id, volume['origin'].snapshot_id))
        else:  # 缩容失败，删除目标盘
            cfg.log.info('[FAILED]%s for %s(Snapshot)' % (volume['origin'].id, volume['origin'].snapshot_id))
            if volume.get('dup', ''):
                volume['dup'].delete()
        volume['origin'].delete()
        return

    # EC2/AMI缩容，磁盘挂回，启动测试
    attach_volume(volume['result'], volume['dev'], instance.id)
    if volume['state'] not in ['pending', 'running']:
        return  # 缩容前原机本就未启动，不做启动测试
    volumes = [v for v in cfg.volumeRepository if v.get('instance', '') == volume['instance']]
    if sum(1 for v in volumes if not v.get('result', '')):
        return  # 原机其他磁盘未完成缩容
    no_check = sum(1 for v in volumes if v.get('result', 'R') == v.get('origin', 'O')) == len(volumes)
    if start_instance(instance, no_check):  # 原盘挂回，不检查启动状态no_check
        return
    restore_instance(instance)  # 启动不正常，恢复原盘系统


# 启动EC2
def start_instance(instance, no_check=False):
    instance.start()
    cfg.log.info('%s starting...' % instance.id)
    if no_check:
        return True

    instance.wait_until_running()
    for i in range(cfg.TIMEOUT):
        r = client_ec2.describe_instance_status(InstanceIds=[instance.id])
        if r['InstanceStatuses'][0]['InstanceStatus']['Status'] == 'ok':
            cfg.log.info('%s started' % instance.id)
            return True
        time.sleep(2)
    else:
        cfg.log.warning('%s failed to start.' % instance.id)
        return False


# 恢复原盘系统
def restore_instance(instance):
    stop_instance(instance, interactive=False)
    for v in cfg.volumeRepository:
        if v.get('instance', '') == instance and v.get('result', 'R') == v.get('dup', 'V'):  # 别的机器或挂的不是新盘
            detach__volume(v['result'])  # 卸下新盘
            attach_volume(v['origin'], v['dev'], instance.id)  # 换上原盘
    start_instance(instance, no_check=True)  # 启动原机


# 返回具有指定tag的快照列表
def snapshot2volume():
    snapshots = ec2.snapshots.filter(Filters=cfg.args.filters + [{'Name': 'status', 'Values': ['completed']}])
    volumes = []
    for snapshot in snapshots:
        volume = create_volume('gp2', SnapshotId=snapshot.id)
        volume.create_tags(Tags=[dict(Key='Origin', Value=snapshot.id),
                                 dict(Key='Project', Value='ShrinkVolume')])
        volumes.append(dict(origin=volume))
    return volumes


# 集群模式的控制节点
def master():
    cfg.log.info('===Shrink Master Node is starting to dispatch task to worker node===')
    user_data = '#!/bin/bash\ncd /home/ec2-user/ShrinkEbs\npython3 shrink.py -o a -o s'

    user_data += ' -o b' if 'b' in cfg.args.omit else ''
    self = ec2.Instance(cfg.instance['instanceId'])
    images = [image for image in ec2.images.filter(Filters=[dict(Name='name', Values=['VolumeShrink'])])]
    if images:
        image = images[0]
        cfg.log.info('%s found for worker nodes' % image.id)

    else:
        image = self.create_image(Description='VolumeShrink tools', Name='VolumeShrink', NoReboot=True)
        cfg.log.info('Creating %s for worker nodes' % image.id)

    image.wait_until_exists()
    for i in range(cfg.TIMEOUT):
        image.load()
        if image.state == 'available':
            break
        time.sleep(3)
    else:
        cfg.log.error('%s(old) failed to remove, exiting' % image.id)
        return

    idx = 0
    for instance in get_instances(same_az=False):
        idx += 1
        worker = ec2.create_instances(InstanceType='c5.large', MaxCount=1, MinCount=1, EbsOptimized=True,
                                      ImageId=image.id,
                                      IamInstanceProfile=dict(Arn=self.iam_instance_profile['Arn']),
                                      Placement={'AvailabilityZone': instance.placement['AvailabilityZone']},
                                      UserData='%s instance-id=%s\nsudo shutdown\n' % (user_data, instance.id),
                                      InstanceInitiatedShutdownBehavior='terminate',
                                      )[0]
        worker.create_tags(Tags=[dict(Key='Name', Value='ShrinkWorker'),
                                 dict(Key='Project', Value='ShrinkVolume'),
                                 dict(Key='ShrinkingFor', Value=instance.id),
                                 ])
        instance.create_tags(Tags=[dict(Key='ShrinkingBy', Value=worker.id)])
        cfg.log.info('%s(worker) is starting to shrink volumes of %s(target)' % (worker.id, instance.id))

    # clear_image(image)
    cfg.log.info('===%s ShrinkWorker is starting... Master Node terminated normally===' % idx)


def clear_image(image):
    snapshots = [ec2.Snapshot(device['Ebs']['SnapshotId']) for device in image.block_device_mappings]
    image.deregister()
    [snapshot.delete() for snapshot in snapshots]
    cfg.log.info('%s and %s deleted.' % (image.id, [s.id for s in snapshots]))


if __name__ == '__main__':
    pass
