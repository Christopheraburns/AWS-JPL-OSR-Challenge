from setuptools import setup, find_packages

setup(
    name="markov",
    version="0.1.1",
    zip_safe=False,
    author="Chris",
    long_description="RL Agent for NASA-Rover-Challenge",
    packages=find_packages(),
    python_requires='>=3.6',
    install_requires=[
       'boto3==1.9.23',
        'futures==3.1.1',
        'gym==0.10.5',
        'kubernetes==7.0.0',
        'minio==4.0.5',
        'numpy==1.14.5',
        'pandas==0.22.0',
        'Pillow==4.3.0',
        'pygame==1.9.3',
        'PyYAML==4.2b1',
        'redis==2.10.6',
        'rospkg==1.1.7',
        'scipy==0.19.0',
        'tensorflow==1.12.2',
        'rl-coach-slim==0.11.1',
    ],
    entry_points = {
        'console_scripts': [
            'run_local_rl_agent=markov.rover_agent:main'
        ],
    }
)