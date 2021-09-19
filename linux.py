#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : linux.py
# @Author: Bai Yanzheng
# @Date  : 2020/4/13
# @Desc  : 对EC2磁盘或快照缩容到实际占用空间的1.2倍
#

import os
import time
from subprocess import Popen, PIPE

import aws
import config as cfg
import grub2

NotErrs = ('mke2fs 1.42.9 (28-Dec-2013)\n',
           'Installing for i386-pc platform.\nInstallation finished. No error reported.\n')


# 拼成linux设备号
def device_name(device_no):
    return '/dev/sd%s' % chr(device_no + 102)


# 拼成linux设备挂载点
def mount_point(device_no):
    return os.path.join(cfg.WorkPath, chr(device_no + 102))


# 执行linux命令并返回标准输出
def cmd(command):
    cfg.log.info(command.replace('\n', '\\n'))
    process = Popen(command, shell=True, stdout=PIPE, stderr=PIPE)
    stdout, stderr = process.communicate()
    stdout, stderr = stdout.decode('utf-8', 'ignore'), stderr.decode('utf-8', 'ignore')
    cfg.log.info(stdout[-100:]) if stdout else None
    cfg.log.error(stderr) if stderr and stderr not in NotErrs else None
    return stdout


# 在子线程中对某块磁盘进行缩容
def shrink(idx_repository):
    """
    :param idx_repository: idx of volume in cfg.Repository
    """
    volume = cfg.volumeRepository[idx_repository]
    success = True
    # rsync复制数据
    for partition in volume['partitions']:
        try:
            if partition.get('raw', False):  # 无法mount的分区，dd直接复制
                cmd('sudo dd if=%s of=%s' % (partition['dev'], partition['dupdev']))
                continue
            source_mp, dst_mp = mount_point(idx_repository), mount_point(idx_repository + cfg.RepCapacity)
            cmd('sudo mount %s %s' % (partition['dev'], source_mp))
            cmd('sudo mount %s %s' % (partition['dupdev'], dst_mp))
            cmd('sudo rsync -a -r %s/ %s/' % (source_mp, dst_mp))
            cmd('sudo umount %s' % source_mp)
            if 'a' in volume['dev'] and os.path.exists('%s/boot' % dst_mp):  # 按需做boot loader
                grub2.install_boot_loader(device_name(idx_repository + cfg.RepCapacity), dst_mp, partition['uuid'])
            cmd('sudo umount %s' % dst_mp)
            # 目标分区uuid和label改为与源相同，保证正常挂载
            label = partition['label']
            cmd('sudo tune2fs -U %s %s%s' %
                (partition['uuid'], '-L %s ' % label if label else ' ', partition['dupdev']))
        except Exception as err:
            cfg.log.exception('Exception occurred, %s%s skipped to shrink.\n%s' %
                              (err, partition['dev'], volume['origin']))
            success = False
            break

    # 摘下源盘及目标盘
    volume['origin'].detach_from_instance()
    volume['dup'].detach_from_instance()
    cfg.log.info('%s(origin) and %s(dup) detached from shrinking server' % (volume['origin'].id, volume['dup'].id))

    volume['result'] = volume['dup'] if success else volume['origin']
    aws.wind_up(idx_repository)


# 源盘挂到本机，估算缩容容量，建新盘并挂载
def attach(idx):
    volume = cfg.volumeRepository[idx]
    vol_origin = volume['origin']
    dev_origin, dev_dup = device_name(idx), device_name(idx + cfg.RepCapacity)  # 组合设备文件/dev/sdf， dev/sdl
    aws.attach_volume(vol_origin, dev_origin, cfg.instance['instanceId'])
    partitions = get_partitions(dev_origin)
    size = get_size(partitions, mount_point(idx))
    usage_rate = size / vol_origin.size
    if usage_rate > 0.7:  # 太满的盘不缩，直接挂回， 该条件包含了无法mount的
        cfg.volumeRepository[idx]['result'] = cfg.volumeRepository[idx]['origin']  # 不做，原盘挂回
        cfg.log.info('%s need not to shrink due to high usage(%d%%)' %(volume['origin'].id, usage_rate * 100))
        aws.wind_up(idx)
        return False
    size = max(size, cfg.MinVolumeSize)  # 保持不要太小
    iops = 0 if vol_origin.volume_type != 'io1' else vol_origin.iops
    volume_dup = aws.create_volume(VolumeType=vol_origin.volume_type, Size=size, Iops=iops)
    volume_dup.create_tags(Tags=[{'Key': 'ShrinkFrom', 'Value': vol_origin.id}, ])
    aws.attach_volume(volume_dup, dev_dup, cfg.instance['instanceId'])
    volume['dup'], volume['partitions'] = volume_dup, partitions
    return True


# 获取磁盘中需要复制的分区(去掉启动分区）
def get_partitions(device):
    """
    :param device: '/dev/sdf'
    :return: [dict(dev=dev, uuid=source.get('UUID', ''), label=source.get('LABEL', ''))]
    """
    partitions = []
    lines = cmd('sudo fdisk --b -l %s|grep -v Disk|grep -v BIOS|grep %s' % (device, device)).strip('\n').split('\n')
    lines = [line for line in lines if line and '4095' not in line.split()]
    for line in lines:
        fields = line.split()
        dev, size = fields[0], int(int(fields[4]) / 1048576) + 1  # 4的位置为不是所有时候都对TBD dos的多一个字段
        fields = {field.split('=')[0]: field.split('=')[-1].strip('"') for field in cmd('sudo blkid %s' % dev).split()}
        partitions.append(dict(dev=dev, uuid=fields.get('UUID', ''), label=fields.get('LABEL', ''), size=size))
    return partitions


# mount源分区，计算缩容后所需总容量
def get_size(partitions, mount_point_):
    """
    :param partitions: [dict(dev=, uuid=, label=)]
    :param mount_point_: '/dev/sdf'
    :return: dest size(GB), partitions: [dict(dev=, uuid=, label=, size=)]
    """
    for partition in partitions:
        cmd('sudo mount %s %s' % (partition['dev'], mount_point_))
        msg = cmd('sudo df -m %s|grep /dev/' % partition['dev']).split()
        if len(msg) < 2:  # 磁盘没有mount上，无文件系统等原因，本分区不处理
            partition['raw'] = True
        else:
            partition['size'] = int(int(msg[2]) * cfg.ExpandFactor) + 1  # MB
        cmd('sudo umount %s' % mount_point_)
    return int(sum(partition['size'] for partition in partitions) / 1024) + 1  # GB


# 新盘建分区和文件系统
def fdisk(vol_idx, partitions):
    """
    :param vol_idx: 102-f,103-g
    :param partitions: [dict(dev=, uuid=, label=, size=)]
    :return: partitions: [dict(dev=, uuid=, label=, size=, dupdev=)]
    """
    device = device_name(vol_idx)
    txt = 'g\nn\n128\n2048\n4095\nt\n4\n'
    for idx, partition in enumerate(partitions):
        txt += 'n\n%d\n\n+%dM\n' % (idx + 1, partition['size']) \
            if idx < len(partitions) - 1 else 'n\n%d\n\n\n' % (idx + 1)
        partition['dupdev'] = '%s%d' % (device, idx + 1)
    txt += 'w\n'
    cmd('sudo echo "%s"|sudo fdisk %s' % (txt, device))
    time.sleep(5)
    for idx, partition in enumerate(partitions):
        if partition.get('raw', False):  # 源分区无法mount的，不做文件系统
            continue
        cmd('sudo mkfs -t ext4 %s' % partition['dupdev'])


if __name__ == '__main__':
    pass
