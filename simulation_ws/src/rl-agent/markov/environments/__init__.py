from gym.envs.registration import register

MAX_STEPS = 1000000

register(
    id='Rover-TrainingGrounds-v2',
    entry_point='markov.environments.training_env:RoverTrainingGroundsDiscreteEnv',
    max_episode_steps = MAX_STEPS,
    reward_threshold = 500
)


register(
    id='Mars-v1',
    entry_point='markov.environments.mars_env:MarsDiscreteEnv',
    max_episode_steps = MAX_STEPS,
    reward_threshold = 2000
)