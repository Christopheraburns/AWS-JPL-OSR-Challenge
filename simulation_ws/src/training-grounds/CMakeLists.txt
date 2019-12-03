cmake_minimum_required(VERSION 2.8.3)
project(training_grounds)

find_package(catkin REQUIRED COMPONENTS
  gazebo_ros
  rover_description    # required to install .rviz model
)

catkin_package(DEPENDS gazebo_ros)

install(DIRECTORY launch 
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)


install(DIRECTORY models/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/models
)
install(DIRECTORY worlds/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/worlds
)

install(PROGRAMS
    scripts/run_local_rl_agent.sh
    DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)