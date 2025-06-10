# grok_plays_pokemon/environment/wrappers/configured_env_wrapper.py
# C
from pathlib import Path
from omegaconf import OmegaConf, DictConfig
from .env_wrapper import EnvWrapper

class ConfiguredEnvWrapper(EnvWrapper):
    def __init__(self, base_conf: DictConfig, cli_args=None):
        # Expect base_conf to be the fully resolved DictConfig from play.py's _setup_configuration
        # The cli_args are kept for now in case they are used by RedGymEnv or other parts of the wrapper,
        # though the primary configuration comes from base_conf.
        self.conf = base_conf 
        super().__init__(self.conf) # Pass the resolved config to the RedGymEnv (via EnvWrapper -> RedGymEnv)