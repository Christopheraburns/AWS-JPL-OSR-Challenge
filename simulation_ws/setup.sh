#!/bin/bash

sudo apt-get update
source /opt/ros/melodic/setup.sh
rosdep update

sudo pip3 install -U awscli
sudo pip3 install -U colcon-common-extensions colcon-ros-bundle
sudo pip3 install boto3

