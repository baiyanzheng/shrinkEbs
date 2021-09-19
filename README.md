# shrinkvolume
Shrink EBS disk of system and data volume on EC2

## 生产服务器配置
- t3.medium
- Amazon Linux 2 AMI (HVM)
- 相同AZ
- 需具有操作EC2权限

## 准备工作
- 为需要缩容的虚机或快照设置标签
- 设置本程序启动命令行参数，指明是否处理系统盘，以及所用标签

## EBS盘缩容原理及过程
1. 按指定标签搜索本AZ内EC2和快照
1. 停机卸载数据盘，可以包含系统启动盘
1. 挂载到本机上，查看占用空间大小
1. 按实占空间1.2倍申请新EBS卷，挂载到本机
1. 按源盘分区数量和实占空间划分区，并留出BIOS分区
1. 制作ext4文件系统，跟源分区一起mount到系统中
1. 用rsync同步两个文件系统数据
1. 如果是系统启动盘，制作并安装Bootloader
1. 分区UUID/Label改成跟源盘一样
1. 新制作的EBS盘原顺序挂回原EC2
1. 启动原EC2
1. 源盘卸下后不自动删除

# Release Notes

## V0.1 20200814 调通版
1. 按需停止EC2，摘盘缩容并把新盘挂回后启动到原状态
1. 
1. 启动命令行：python main -i|-s|-e|-d tag=value
* -i 交互式，虚机是否停机缩容要求交互式确认
* -s 同时对EC2系统盘缩容
* -e 对具有对应标签的EC2磁盘缩容
* -d 对具有对应标签的快照缩容， -e/-d都不指定时全部缩容，等同于都指定
* tag=value指定缩容设备所需的标签和取值

## V0.2 20200814 
1. [o]改用全局对象cfg.volumeRepository维护缩容盘列表 
2. [r]用量超过70%的盘不缩，提高效率
3. [r]缩容后EC2x虚机启动失败时，恢复原盘启动

## V0.2 20200815
1. [o]改用argparse包优化命令行参数处理，支持AWS各种filter条件(tag:name形式表示tag)
1. [r]对符合条件的AMI缩容成磁盘，通过tag:ami记录对应AMI
1. [r]创建EBS盘改为动态传参
1. [r]对快照缩容

## V0.2 20200816
1. [r]增加master节点程序，支持并行处理：为每一台需要缩容的EC2启动一台工作节点
1. [o]运行日志写入文件后，部署CloudWatch Agent采集到CloudWatch log中集中审查
1. [o]增加安装脚本和说明
1. [r]无法mount的源文件系统用dd复制

## V0.3 20200816完整版
1. [r]系统盘制作Boot loader(Grub2), 原启动盘支持grub1, grub2
1. [b]rsync无法复制目录t属性[改成-a -r]
1. [b]线程启动前就退回不缩的盘， task为空
1. [r]增加了缩减总容量的日志记录
1. [b] 安装了python3.7之后制作的镜像，user data在os里能看到，但是不运行[clound-init是python2程序，不能把/bin/python指到python3]

## 代办任务
1. [b] 非ext4的改/etc/fstab, 或者用原文件系统格式
1. [r] Windows
1. [r] 先从快照缩容全量，再停机更新增量，缩短停机时间
1. [b]redhat镜像是grub2+dos分区，但配置文件中没有linux16,只有set default_kernelopts="root=/... [到/boot/loader/*.conf去找]
