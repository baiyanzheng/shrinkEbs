#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : config.py
# @Author: Bai Yanzheng
# @Date  : 2020/4/12
# @Desc  : 全局变量和公共函数
#

import json
import logging
import os
import sys
from linux import cmd

# 常量参数定义
WorkPath = '/tmp/shrink'
RepCapacity = 6  # 并发处理此数量的磁盘缩容
MinVolumeSize = 5  # 小于此容量(GB)的盘不缩容
ExpandFactor = 1.2  # 缩容到实战容量的比例
TIMEOUT = 30  # AWS服务超时次数/秒数
YES = ['', 'y', 'Y']

# 全局变量定义
log = logging.getLogger()  # 运行日志句柄
args = {}  # 命令行参数dict{filters=[{'Name': 'tag:Project', 'Values': []}], interactive=False, omit=['a'], omit=[]}
instance = {}  # 本机的AWS参数
'''
{
    'accountId': '002221862146', 
    'architecture': 'x86_64', 
    'availabilityZone': 'cn-northwest-1b', 
    'billingProducts': None, 
    'devpayProductCodes': None, 
    'marketplaceProductCodes': None, 
    'imageId': 'ami-0934e7d625575bb7c', 
    'instanceId': 'i-0ce053e51142c45fb', 
    'instanceType': 'c5.large', 
    'kernelId': None, 
    'pendingTime': '2020-08-12T00:41:11Z', 
    'privateIp': '172.31.25.9', 
    'ramdiskId': None, 
    'region': 'cn-northwest-1', 
    'version': '2017-09-30'}
'''
volumeRepository = []  # 处理中的磁盘仓库
"""
dict(origin=volume,
     dup=None,
     partitions = [dict(dev=, uuid=, label=, size=, dupdev=)],
     task=None,
     instance=instance, 
     dev='dev/sdf', 
     state='Running'，
     result='dup|origin|'
"""


def setup(args_=None):
    global log, args, instance

    # 创建/tmp/shrink/f...目录，用于挂载磁盘
    None if os.path.exists(WorkPath) else os.mkdir(WorkPath)
    None if os.path.exists(os.path.join(WorkPath, 'boot')) else cmd('sudo cp -r /boot /tmp/shrink/boot')
    for f in range(102, 102 + RepCapacity * 2):
        if not os.path.exists(os.path.join(WorkPath, chr(f))):
            os.mkdir(os.path.join(WorkPath, chr(f)))

    # 初始化日志
    log = logging.getLogger()
    log.setLevel(dict(NOTSET=0, DEBUG=10, INFO=20, WARNING=30, ERROR=40, CRITICAL=50)
                 .get(os.environ.get('log', 'INFO').upper(), 20))
    filename = '%s.log' % os.path.splitext(os.path.split(sys.argv[0])[1])[0]
    fh = logging.FileHandler(os.path.join(WorkPath, filename), encoding='GBK')
    fh.setFormatter(logging.Formatter('%(asctime)s\t%(levelname)s\t%(filename)s %(lineno)d\t%(message)s'))
    log.addHandler(fh)

    # 存储命令行参数
    args = args_

    # 获取本机参数
    instance = json.loads(cmd('curl -s 169.254.169.254/latest/dynamic/instance-identity/document'))
    os.environ['AWS_DEFAULT_REGION'] = instance['region']  # 设置工作region


# 多级字典中，增加一行的key和默认值
def cascadeDictDefault(dictionary, key_defaults):
    for key, default in key_defaults:
        if key not in dictionary.keys():
            dictionary[key] = default
        dictionary = dictionary[key]
    return dictionary


