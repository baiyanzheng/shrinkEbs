#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @File  : grub2.py
# @Author: Bai Yanzheng
# @Date  : 2020/4/13
# @Desc  : 复制原盘内核等启动信息，制作grub2 boot loader.
#

import os
import datetime
import config as cfg
import linux


# 获取grub1/grub2启动的kernel等配置信息
def grub(grub_cfg_file, configure):
    """
    :param grub_cfg_file: grub.conf
    :param configure: dict(kernel=['kernel'], boot=['initrd'])
    :return: dict(kernel='/boot/...', boot='/boot/...')
    """
    grub_cfg = {}
    for line in open(grub_cfg_file, 'r', encoding='utf-8'):
        fields = line.split(maxsplit=1)
        if len(fields) < 2:
            continue
        for parameter, names in configure.items():
            if fields[0] not in names:
                continue
            grub_cfg[parameter] = fields[1]
            if set(grub_cfg.keys()) == set(configure.keys()):  # 找全第一组参数，退出
                return grub_cfg
            break

    raise UserWarning('Insufficient Parameters %s' % (set(grub_cfg.keys()) - set(configure.keys())))  # 缺少参数


BootLoaders = [
    dict(prog=grub, cfg='boot/grub/grub.conf', result=dict(kernel=['kernel'], boot=['initrd'])),
    dict(prog=grub, cfg='boot/grub2/grub.cfg', result=dict(kernel=['linux16'], boot=['initrd16'])),
]
Grub2Header = '#\n# grub2 config for Shrink Tool\n# Created on %s by shrink.py\n#\n\n' % datetime.date.today()

default_kernelopts = ''  # 有的配置只有这样一行，写kernel的参数，命令在另外文件里


# 创建grub.cfg文件
def create_config(grub_cfg, uuid):
    header = Grub2Header + 'UUID=%s\nKERNAL="%s"\nIMG="%s"\n' % (uuid, grub_cfg['kernel'], grub_cfg['boot'])
    tmp_config, dst_config = os.path.join(cfg.WorkPath, 'grub.cfg'), os.path.join(cfg.WorkPath, 'boot/grub2/grub.cfg')
    linux.cmd('rm -f %s' % tmp_config) if os.path.exists(tmp_config) else None

    with open(tmp_config, 'w', encoding='utf-8') as fp:
        fp.write(header)
        content = open('./grub.cfg', 'r', encoding='utf-8').read()
        fp.write(content)
    linux.cmd('sudo chmod 600 %s && sudo chown root:root %s && sudo mv -f %s %s' %
              (tmp_config, tmp_config, tmp_config, dst_config))


# 安装grub2的boot loader
def install_boot_loader(dev, mount_point, uuid):
    for boot_loader in BootLoaders:
        config_file = os.path.join(mount_point, boot_loader['cfg'])
        if not os.path.exists(config_file):
            continue
        result = boot_loader['prog'](config_file, boot_loader['result'])  # 找到原grub的配置文件，抽出内核文件等信息
        for command in result.values():  # 复制kernel等文件
            file_from = command.strip().split(maxsplit=1)[0].strip('/').strip('\\')
            file_to = os.path.join(cfg.WorkPath, file_from)
            file_from = os.path.join(mount_point, file_from)
            if not os.path.exists(file_from):
                raise UserWarning('%s not found.' % file_from)
            linux.cmd('sudo \cp %s %s' % (file_from, file_to))
        create_config(result, uuid)  #
        path = os.path.join(mount_point, 'boot')
        linux.cmd('sudo rm -rf %s' % path)
        linux.cmd('sudo cp -r %s %s' % (os.path.join(cfg.WorkPath, 'boot'), path))
        linux.cmd('sudo grub2-install --boot-directory=%s %s' % (path, dev))
        break
    else:
        raise UserWarning('No supported boot loader found.')


if __name__ == '__main__':
    grub('/tmp/shrink/grub.cfg', BootLoaders[0]['result'])
    install_boot_loader('/dev/sdg', '/tmp/shrink/l', 'bc9333e6-4406-4772-b046-c8c614c59287')
