from __future__ import print_function

import time
import boto3
import gym
import numpy as np
from gym import spaces
import PIL
from PIL import Image
import os
import random
import math
import sys
import rospy
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist, Pose, Quaternion
from gazebo_msgs.srv import SetModelState, SetModelConfiguration
from gazebo_msgs.msg import ModelState, ContactsState
from sensor_msgs.msg import Image as sensor_image
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Point
from std_msgs.msg import Float64
from std_msgs.msg import String
from PIL import Image
import queue


VERSION = "0.0.4"
#TRAINING_IMAGE_SIZE = 360 #Ue Lidar scanner which look around 360 degree
TRAINING_IMAGE_WIDTH = 160
TRAINING_IMAGE_HEIGHT = 120
TRAINING_IMAGE_SIZE = (TRAINING_IMAGE_WIDTH, TRAINING_IMAGE_HEIGHT)

LIDAR_SCAN_MAX_DISTANCE = 4.5  # Max distance Lidar scanner can measure
CRASH_DISTANCE = 0.49  # Min distance to obstacle (The LIDAR is in the center of the 1M Rover)

# Size of the image queue buffer, we want this to be one so that we consume 1 image
# at a time, but may want to change this as we add more algorithms
IMG_QUEUE_BUF_SIZE = 1


# REWARD Multipliers
COLLISION_REWARD = 0
FINISHED_REWARD = 10000
MIDPOINT_REWARD = 5000

# Prevent unknown "stuck" scenarios with a kill switch (MAX_STEPS)
MAX_STEPS = 500

# Destination Point
CHECKPOINT_X = 8.5
CHECKPOINT_Y = 8.5

# Initial position of the robot
MIDPOINT_X = 0
MIDPOINT_Y = 0
INITIAL_POS_X = -8.5
INITIAL_POS_Y = -8.5
INITIAL_POS_Z = .65
INITIAL_ORIENT_X = 0.0174910797633
INITIAL_ORIENT_Y = 0.0178586654381
INITIAL_ORIENT_Z = -0.699497425461
INITIAL_ORIENT_W = 0.71419778911

# Initial distance to checkpoint
INITIAL_DISTANCE_TO_CHECKPOINT = abs(math.sqrt(((CHECKPOINT_X - INITIAL_POS_X) ** 2) +
                                               ((CHECKPOINT_Y - INITIAL_POS_Y) ** 2)))

# Corner boundaries of the world (in Meters)
STAGE_X_MIN = -10.0
STAGE_Y_MIN = -10.0
STAGE_X_MAX = 10.0
STAGE_Y_MAX = 10.0


# SLEEP INTERVALS
SLEEP_AFTER_RESET_TIME_IN_SECOND = 0.3
SLEEP_BETWEEN_ACTION_AND_REWARD_CALCULATION_TIME_IN_SECOND = 0.3 # LIDAR Scan is 5 FPS (0.2sec).
SLEEP_WAITING_FOR_IMAGE_TIME_IN_SECOND = 0.01


class RoverTrainingGroundsEnv(gym.Env):
    def __init__(self):
        self.x = INITIAL_POS_X
        self.y = INITIAL_POS_Y
        self.orientation = None
        self.aws_region = 'us-east-1'
        self.reward_in_episode = 0
        self.steps = 0
        self.last_collision_threshold = sys.maxsize
        self.last_position_x = INITIAL_POS_X
        self.last_position_y = INITIAL_POS_Y
        self.distance_travelled = 0
        self.current_distance_to_checkpoint = INITIAL_DISTANCE_TO_CHECKPOINT
        self.collision_threshold = sys.maxsize
        self.collision = False
        self.state = None
        self.steering = 0
        self.throttle = 0
        self.closer_to_checkpoint = False
        self.power_supply_range = MAX_STEPS
        self.reached_midpoint = False


        # action space -> steering angle, throttle
        self.action_space = spaces.Box(low=np.array([-1, 0]), high=np.array([+1, +3]), dtype=np.float32)


        # Create the observation space
        self.observation_space = spaces.Box(low=0, high=255,
                                            shape=(TRAINING_IMAGE_SIZE[1], TRAINING_IMAGE_SIZE[0], 3),
                                            dtype=np.uint8)

        self.state = None
        self.image_queue = queue.Queue(IMG_QUEUE_BUF_SIZE)

        # ROS initialization
        self.ack_publisher = rospy.Publisher('/cmd_vel', Twist, queue_size=100)

        # TODO - Remove these publishers before release - they are for debugging only
        self.current_position_pub = rospy.Publisher('/current_position', Point, queue_size=3)
        self.distance_travelled_pub = rospy.Publisher('/distance_travelled', String, queue_size=3)
        # ################################################################################

        self.gazebo_model_state_service = rospy.ServiceProxy('/gazebo/set_model_state', SetModelState)
        self.gazebo_model_configuration_service = rospy.ServiceProxy('/gazebo/set_model_configuration', SetModelConfiguration)
        rospy.init_node('rl_coach', anonymous=True)

        # Subscribe to ROS topics and register callbacks
        rospy.Subscriber('/odom', Odometry, self.callback_pose)
        rospy.Subscriber('/scan', LaserScan, self.callback_scan)
        rospy.Subscriber('/robot_bumper', ContactsState, self.callback_collision)
        rospy.Subscriber('/camera/image_raw', sensor_image, self.callback_image)


    '''
    DO NOT EDIT - Function called by rl_coach to instruct the agent to take an action
    '''
    def step(self, action):
        # initialize rewards, next_state, done
        self.reward = None
        self.done = False
        self.next_state = None

        steering = float(action[0])
        throttle = float(action[1])
        self.steps += 1
        self.send_action(steering, throttle)
        time.sleep(SLEEP_BETWEEN_ACTION_AND_REWARD_CALCULATION_TIME_IN_SECOND)

        self.call_reward_function(action)

        info = {}  # additional data, not to be used for training


        #return dict(xy=np.array([self.next_state[0]]), observation=np.array([self.next_state[1]]), theta=np.array([self.next_state[2]]) ), self.reward, self.done, {}

        return self.next_state, self.reward, self.done, info

    '''
    DO NOT EDIT - Function called at the conclusion of each episode to reset episodic values
    '''
    def reset(self):
        print('Total Episodic Reward=%.2f' % self.reward_in_episode,
              'Total Episodic Steps=%.2f' % self.steps)
        self.send_reward_to_cloudwatch(self.reward_in_episode)

        # Reset global episodic values
        self.reward = None
        self.done = False
        self.next_state = None
        self.ranges= None
        self.send_action(0, 0) # set the throttle to 0
        self.rover_reset()
        self.call_reward_function([0, 0])

        return self.next_state

    '''
    DO NOT EDIT - Function called to send the agent's chosen action to the simulator (Gazebo)
    '''
    def send_action(self, steering, throttle):
        speed = Twist()
        speed.linear.x = throttle
        speed.angular.z = steering
        self.ack_publisher.publish(speed)

    '''
    DO NOT EDIT - Function to reset the rover to the starting point in the world
    '''
    def rover_reset(self):
        
        # Reset Rover-related Episodic variables
        rospy.wait_for_service('gazebo/set_model_state')

        self.x = INITIAL_POS_X
        self.y = INITIAL_POS_Y

        # Put the Rover at the initial position
        model_state = ModelState()
        model_state.pose.position.x = INITIAL_POS_X
        model_state.pose.position.y = INITIAL_POS_Y
        model_state.pose.position.z = INITIAL_POS_Z
        model_state.pose.orientation.x = INITIAL_ORIENT_X
        model_state.pose.orientation.y = INITIAL_ORIENT_Y
        model_state.pose.orientation.z = INITIAL_ORIENT_Z
        model_state.pose.orientation.w = INITIAL_ORIENT_W
        model_state.twist.linear.x = 0
        model_state.twist.linear.y = 0
        model_state.twist.linear.z = 0
        model_state.twist.angular.x = 0
        model_state.twist.angular.y = 0
        model_state.twist.angular.z = 0
        model_state.model_name = 'rover'

        # List of joints to reset (this is all of them)
        joint_names_list = ["rocker_left_corner_lb",
                            "rocker_right_corner_rb",
                            "body_rocker_left",
                            "body_rocker_right",
                            "rocker_right_bogie_right",
                            "rocker_left_bogie_left",
                            "bogie_left_corner_lf",
                            "bogie_right_corner_rf",
                            "corner_lf_wheel_lf",
                            "imu_wheel_lf_joint",
                            "bogie_left_wheel_lm",
                            "imu_wheel_lm_joint",
                            "corner_lb_wheel_lb",
                            "imu_wheel_lb_joint",
                            "corner_rf_wheel_rf",
                            "imu_wheel_rf_joint",
                            "bogie_right_wheel_rm",
                            "imu_wheel_rm_joint",
                            "corner_rb_wheel_rb",
                            "imu_wheel_rb_joint"]
        # Angle to reset joints to
        joint_positions_list = [0 for _ in range(len(joint_names_list))]

        self.gazebo_model_state_service(model_state)
        self.gazebo_model_configuration_service(model_name='rover', urdf_param_name='rover_description', joint_names=joint_names_list, joint_positions=joint_positions_list)

        self.last_collision_threshold = sys.maxsize
        self.last_position_x = self.x
        self.last_position_y = self.y

        time.sleep(SLEEP_AFTER_RESET_TIME_IN_SECOND)

        self.distance_travelled = 0
        self.current_distance_to_checkpoint = INITIAL_DISTANCE_TO_CHECKPOINT
        self.steps = 0
        self.reward_in_episode = 0
        self.collision = False
        self.closer_to_checkpoint = False
        self.power_supply_range = MAX_STEPS
        self.reached_midpoint = False
        # First clear the queue so that we set the state to the start image
        _ = self.image_queue.get(block=True, timeout=None)
        self.set_next_state()

    '''
    DO NOT EDIT - Function to find the distance between the rover and nearest object within 4.5M via LIDAR
    '''
    def get_distance_to_object(self):

        while not self.ranges:
            time.sleep(SLEEP_WAITING_FOR_IMAGE_TIME_IN_SECOND)

        size = len(self.ranges)
        x = np.linspace(0, size - 1, 360)
        xp = np.arange(size)
        val = np.clip(np.interp(x, xp, self.ranges), 0, LIDAR_SCAN_MAX_DISTANCE)
        val[np.isnan(val)] = LIDAR_SCAN_MAX_DISTANCE

        # Find min distance
        self.collision_threshold = np.amin(val)


    '''
    DO NOT EDIT - Function to resize the image from the camera and set observation_space
    '''
    def set_next_state(self):
        try:
            # Make sure the first image is the starting image
            image_data = self.image_queue.get(block=True, timeout=None)
        
            # Read the image and resize to get the state
            image = Image.frombytes('RGB', (image_data.width, image_data.height), image_data.data, 'raw', 'RGB', 0, 1)
            image = image.resize((TRAINING_IMAGE_WIDTH,TRAINING_IMAGE_HEIGHT), PIL.Image.ANTIALIAS)
            
            # TODO - can we crop this image to get additional savings?
           
            self.next_state = np.array(image)
        except Exception as err:
            print("Error!::set_next_state:: {}".format(err))


    '''
    DO NOT EDIT - Reward Function buffer
    '''
    def call_reward_function(self, action):
        self.get_distance_to_object() #<-- Also evaluate for sideswipe and collistion damage
        
        # Get the observation
        self.set_next_state()
        
        # reduce power supply range
        self.power_supply_range = MAX_STEPS - self.steps
        
        # calculate reward
        reward, done = self.reward_function()

        # Accumulate reward for the episode
        self.reward_in_episode += reward

    
        print('Step:%.2f' % self.steps,
              'Steering:%f' % action[0],
              'R:%.2f' % reward,                                # Reward
              'DTCP:%f' % self.current_distance_to_checkpoint,  # Distance to Check Point
              'DT:%f' % self.distance_travelled,                # Distance Travelled
              'CT:%.2f' % self.collision_threshold,             # Collision Threshold
              'CTCP:%f' % self.closer_to_checkpoint,            # Is closer to checkpoint
              'PSR: %f' % self.power_supply_range)              # Steps remaining in Episode

        self.reward = reward
        self.done = done

        self.last_position_x = self.x
        self.last_position_y = self.y


    '''
    EDIT - but do not change the function signature. 
    Must return a reward value as a float 
    Must return a boolean value indicating if episode is complete
    Must be returned in order of reward, done
    '''
    def reward_function(self):
        '''
        :return: reward as float
                 done as boolean
        '''
        reward = 0
        base_reward = 2
        multiplier = 0
        done = False
        
        
        if self.steps > 0:
            
            # Check for episode ending events first
            # ###########################################
            
            # Has LIDAR registered a hit
            if self.collision_threshold <= CRASH_DISTANCE:
                print("Rover has sustained sideswipe damage")
                return 0, True # No reward
            
            # Have the gravity sensors registered too much G-force
            if self.collision:
                print("Rover has collided with an object")
                return 0, True # No reward
            
            # Has the rover reached the max steps
            if self.power_supply_range < 1:
                print("Rover's power supply has been drained (MAX Steps reached")
                return 0, True # No reward
            
            # Has the Rover reached the Checkpoint
            if self.last_position_x >= CHECKPOINT_X and self.last_position_y >= CHECKPOINT_Y:
                print("Congratulations! The rover has reached the checkpoint!")
                multiplier = FINISHED_REWARD
                reward = (base_reward * multiplier) / self.steps # <-- incentivize to reach checkpoint in fewest steps
                return reward, True
            
            # If it has not reached the check point is it still on the map?
            if self.x < (STAGE_X_MIN - .45) or self.x > (STAGE_X_MAX + .45):
                print("Rover has left the mission map!")
                return 0, True
                
                
            if self.y < (STAGE_Y_MIN - .45) or self.y > (STAGE_Y_MAX + .45):
                print("Rover has left the mission map!")
                return 0, True
            
            
            # No Episode ending events - continue to calculate reward
            
            if self.last_position_x >= MIDPOINT_X and self.last_position_y >= MIDPOINT_Y: # Rover is past the midpoint
                # Determine if Rover already received one time reward for reaching the midpoint
                if not self.reached_midpoint:  
                    self.reached_midpoint = True
                    print("Congratulations! The rover has reached the midpoint!")
                    multiplier = MIDPOINT_REWARD 
                    reward = (base_reward * multiplier)/ self.steps # <-- incentivize to reach mid-point in fewest steps
                    return reward, False
                
            
            # To reach this point in the function the Rover has either not yet reached the mid-point OR has already gotten the one time reward for reaching midpoint
               
            # multiply the reward based on the Rover's proximity to the Checkpoint
            waypoint_interval = INITIAL_DISTANCE_TO_CHECKPOINT / 5 
           
            marker = [waypoint_interval,(waypoint_interval * 2),(waypoint_interval * 3),(waypoint_interval * 4)]
                
            # Get the Base multiplier
            if self.current_distance_to_checkpoint <= marker[0]:
                multiplier = 5
            elif self.current_distance_to_checkpoint <= marker[1] and self.current_distance_to_checkpoint > marker[0]:
                multiplier = 4
            elif self.current_distance_to_checkpoint <= marker[2] and self.current_distance_to_checkpoint > marker[1]:
                multiplier = 3
            elif self.current_distance_to_checkpoint <= marker[3] and self.current_distance_to_checkpoint > marker[2]:
                multiplier = 2
            else:
                multiplier = 1
            
            # Incentivize the rover to stay away from objects
            if self.collision_threshold >= 2.0:      # very safe distance
                multiplier = multiplier + 1
            elif self.collision_threshold < 2.0 and self.collision_threshold >= 1.5: # pretty safe
                multiplier = multiplier + .5
            elif self.collision_threshold < 1.5 and self.collision_threshold >= 1.0: # just enough time to turn
                multiplier = multiplier + .25
            else:
                multiplier = multiplier # probably going to hit something and get a zero reward
            
            # Incentize the rover to move towards the Checkpoint and not away from the checkpoint
            if not self.closer_to_checkpoint:
                if multiplier > 0:
                    # Cut the multiplier in half
                    multiplier = multiplier/2
                    
            reward = base_reward * multiplier
            
        
        return reward, done
    
        
    '''
    DO NOT EDIT - Function to receive LIDAR data from a ROSTopic
    '''
    def callback_scan(self, data):
        self.ranges = data.ranges


    def callback_image(self, data):
        try:
            #rint("Height: {}   Width: {}".format(data.height, data.width))
            self.image_queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception as ex:
           print("Error! {}".format(ex))

    '''
    DO NOT EDIT - Function to receive Position/Orientation data from a ROSTopic
    '''
    def callback_pose(self, data):
        self.orientation = data.pose.pose.orientation
        self.linear_trajectory = data.twist.twist.linear
        self.angular_trajectory = data.twist.twist.angular

        new_position = data.pose.pose.position

        p = Point(new_position.x, new_position.y, new_position.z)

        # Publish current position
        self.current_position_pub.publish(p)

        # Calculate total distance travelled
        dist = math.hypot(new_position.x - self.x, new_position.y - self.y)
        self.distance_travelled += dist
        #self.distance_travelled_pub.publish(str(self.distance_travelled))

        # Calculate remaining power supply range
        #self.power_supply_range -= dist

        # Calculate the distance to checkpoint
        new_distance_to_checkpoint = Float64
        new_distance_to_checkpoint.data = abs(math.sqrt(((new_position.x - CHECKPOINT_X) ** 2) +
                                                        (new_position.y - CHECKPOINT_Y) ** 2))

        if new_distance_to_checkpoint.data < self.current_distance_to_checkpoint:
            self.closer_to_checkpoint = True
        else:
            self.closer_to_checkpoint = False

        # Update the distance to checkpoint
        self.current_distance_to_checkpoint = new_distance_to_checkpoint.data

        # update the current position
        self.x = new_position.x
        self.y = new_position.y

    '''
    DO NOT EDIT - Function to receive Collision data from a ROSTopic
    '''
    def callback_collision(self, data):
        # Listen for a collision with anything in the environment
        collsion_states = data.states
        if len(collsion_states) > 0:
            self.collide = True

    '''
    DO NOT EDIT - Function to wrote episodic rewards to CloudWatch
    '''
    def send_reward_to_cloudwatch(self, reward):
        try:
            session = boto3.session.Session()
            cloudwatch_client = session.client('cloudwatch', region_name=self.aws_region)
            cloudwatch_client.put_metric_data(
                MetricData=[
                    {
                        'MetricName': 'Episode_Reward',
                        'Unit': 'None',
                        'Value': reward
                    },
                    {
                        'MetricName': 'Episode_Steps',
                        'Unit': 'None',
                        'Value': self.steps,
                    },
                    {
                        'MetricName': 'DistanceToCheckpoint',
                        'Unit': 'None',
                        'Value': self.current_distance_to_checkpoint
                    }
                ],
                Namespace='AWS_NASA_JPL_OSR_Challenge'
            )
        except Exception as err:
            print("Error in the send_reward_to_cloudwatch function: {}".format(err))

'''
DO NOT EDIT - Inheritance class to convert discrete actions to continuous actions
'''


class RoverTrainingGroundsDiscreteEnv(RoverTrainingGroundsEnv):
    def __init__(self):
        RoverTrainingGroundsEnv.__init__(self)
        print("New Training Grounds Gym environment created...")
        # actions -> straight, left, right
        self.action_space = spaces.Discrete(3)

    def step(self, action):
        # Convert discrete to continuous
        if action == 0:  # turn left
            steering = 1.0
            throttle = 3.00
        elif action == 1:  # turn right
            steering = -1.0
            throttle = 3.00
        elif action == 2:  # straight
            steering = 0
            throttle = 3.00
        else:  # should not be here
            raise ValueError("Invalid action")

        continuous_action = [steering, throttle]

        return super().step(continuous_action)