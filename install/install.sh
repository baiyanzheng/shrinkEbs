#!/usr/bin/env bash
# Amazon Linux 2 AMI (HVM), SSD Volume Type
# t3.medium
# aws iam role with AmazonEC2FullAccess, CloudWatchAgentServerPolicy and iam:PassRol privilege

# install python3 and library
sudo yum -y install python3.7
cd /bin
sudo ln -s -f python3 python
sudo ln -s -f pip3 pip
sudo pip install boto3

#  install and config aws cloud watch agent
cd
wget https://s3.cn-north-1.amazonaws.com.cn/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
sudo rpm -U ./amazon-cloudwatch-agent.rpm
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-config-wizard
# sudo mv config.json /opt/aws/amazon-cloudwatch-agent/bin/config.json
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json
