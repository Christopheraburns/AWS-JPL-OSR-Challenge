cmake_minimum_required(VERSION 2.8.3)

project(rover_description)

find_package(catkin REQUIRED)

catkin_package()

find_package(roslaunch)

catkin_install_python(PROGRAMS
  nodes/motion
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY config meshes urdf
    DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/${dir}
)
