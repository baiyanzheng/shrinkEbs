#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : shrink.py
# @Author: Bai yanzheng
# @Date  : 2020/4/12
# @Desc  : 对EC2磁盘或快照缩容到实际占用空间的1.2倍
# 输入：EC2/快照/AMI的过滤条件，如tag:tagName = tagValue
# 逻辑：1. 扫描符合条件的实例/快照/AMI
#       2. 打标签，停机，摘盘，挂盘，看实际空间
#       3. 建新盘，挂盘，做文件系统
#       4. 同步数据
#       5. 摘盘、挂回

import argparse
import threading
import config as cfg
import aws
import linux


reducedGB = 0  # 总共缩减的容量


# 设置命令行参数解析格式
def args_parser():
    parser = argparse.ArgumentParser(description='Shrunk volume from ec2/snapshot/ami.',
                                     epilog="\n\n")
    parser.add_argument('-v', '--version', action='version', version='%(prog)s 1.0 @Ultrapower')
    parser.add_argument('filters', nargs='*', metavar='Name=Value1,Value2...',
                        help='aws filters such as tagName=tagValue')
    parser.add_argument('-i', '--interactive', action='store_true', help='interactive mode')
    parser.add_argument('-o', '--omit', action='append', help='omit AMI|EC2|Snapshot|Boot disk',
                        metavar='a|e|s|b', choices=['a', 'e', 's', 'b'])
    parser.add_argument('-m', '--master', action='store_true', help='master node for parallel processing')
    args = parser.parse_args()
    filters = {}
    for filter_ in args.filters:
        kv = filter_.split('=')
        if len(kv) != 2:
            continue
        k = cfg.cascadeDictDefault(filters, ((kv[0], set()),))
        k |= set(kv[1].split(','))

    args.filters = [dict(Name=name, Values=list(values)) for name, values in filters.items()]
    args.omit = set(args.omit) if args.omit else set()

    return args


# 对cfg.volumeRepository中来源于EC2或快照的EBS盘进行缩容，并挂回/启动原EC2
def shrink():
    global reducedGB
    # 创建线程池
    msg = ['%s on %s %s%s' % (v['origin'].id, v.get('state', ''),
                              v['instance'].id if v.get('instance', '') else 'snapshot',
                              v.get('dev', '')) for v in cfg.volumeRepository]
    cfg.log.info('===Begin shrinking for %s' % msg)
    for idx, volume in enumerate(cfg.volumeRepository):
        try:
            if not linux.attach(idx):
                continue
            linux.fdisk(idx + cfg.RepCapacity, volume['partitions'])
            task = threading.Thread(target=linux.shrink, args=(idx,))
            volume['task'] = task
            # linux.shrink(idx)
            task.start()
        except Exception as err:
            cfg.log.error('Exception occurred, %s failed to shrink.' % volume['origin'].id)
            cfg.log.exception(err)

    msg = '\n'
    for volume in cfg.volumeRepository:
        if 'task' in volume:
            volume['task'].join()
        origin = volume['origin']
        if volume.get('result', 'R') == volume.get('dup', 'D'):
            size = int((origin.size - volume['dup'].size))
            result = 'Reduced %dGB' % size
        else:
            size, result = 0, 'NoShrink'
        reducedGB += size
        instance_id = volume['instance'].id if volume.get('instance', '') else 'snapshot'
        msg += '%s[%s] on %s %s%s\n' % (origin.id, result, volume.get('state', ''), instance_id, volume.get('dev', ''))
    cfg.log.info('===End shrinking for %s' % msg)

    cfg.volumeRepository = []  # 清空缩盘库


def shrink_images():
    instances = []
    for image in aws.get_images():
        instance = aws.image2instance(image.id)
        if not aws.stop_instance(instance, interactive=False):
            instance.terminate()
            cfg.log.error('[FAILED]%s from %s failed to stop, ignore shrinking...' % (instance.id, image.id))
            continue
        instances.append(instance)
        volumes = aws.get_instance_volumes(instance)
        if len(cfg.volumeRepository) + len(volumes) >= cfg.RepCapacity:
            shrink()
        aws.detach_volumes(volumes, tags=[dict(Key='AMI', Value=image.id)])
        cfg.volumeRepository += volumes
    shrink()

    for instance in instances:
        instance.terminate()
        cfg.log.info('%s(tmp for AMI) terminated.' % instance.id)


def shrink_ec2s():
    for instance in aws.get_instances():
        volumes = aws.get_instance_volumes(instance)
        if len(cfg.volumeRepository) + len(volumes) >= cfg.RepCapacity:
            shrink()
        if not aws.stop_instance(instance, interactive=cfg.args.interactive):
            cfg.log.error('[FAILED]%s failed to stop, ignore shrinking...' % instance.id)
            continue
        aws.detach_volumes(volumes)
        cfg.volumeRepository += volumes
    shrink()


def shrink_snapshots():
    for volume in aws.snapshot2volume():
        cfg.volumeRepository.append(volume)
        if len(cfg.volumeRepository) >= cfg.RepCapacity:
            shrink()
    shrink()


if __name__ == '__main__':
    cfg.setup(args_parser())
    aws.setup()

    if cfg.args.master and 'e' not in cfg.args.omit:  # master node for parallel process of shrinking EC2
        aws.master()
        exit(0)

    if 'a' not in cfg.args.omit:  # AMI
        shrink_images()
    if 'e' not in cfg.args.omit:  # EC2
        shrink_ec2s()
    if 's' not in cfg.args.omit:  # Snapshot
        shrink_snapshots()

    if reducedGB:
        cfg.log.info('TOTALLY reduced %dGB Storage!' % reducedGB)


