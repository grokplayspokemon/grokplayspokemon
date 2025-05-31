# grok_plays_pokemon/environment/wrappers/configured_env_wrapper.py
import yaml
from pathlib import Path
from omegaconf import OmegaConf, DictConfig
from .env_wrapper import EnvWrapper

class ConfiguredEnvWrapper(EnvWrapper):
    def __init__(self, base_conf, cli_args=None, config_path=None):
        if isinstance(base_conf, DictConfig):
            # If base_conf is already a DictConfig, assume it's fully resolved
            # by the caller (e.g., play.py's _setup_configuration)
            merged = base_conf
        else:
            # Original logic: load base_conf, load YAML, then merge
            base_node = OmegaConf.create(base_conf)

            cfg_file = config_path or (Path(__file__).parent.parent / "config.yaml")
            yaml_conf_content = {}
            if cfg_file.is_file():
                with open(cfg_file) as f:
                    full = yaml.safe_load(f) or {}
                root_conf = {k: v for k, v in full.items() if k not in ("env", "env_config")}
                env_section = full.get("env", {})
                env_config_section = full.get("env_config", {})
                yaml_conf_content = {**root_conf, **env_section, **env_config_section}
            
            yaml_omega_conf = OmegaConf.create(yaml_conf_content)
            merged = OmegaConf.merge(yaml_omega_conf, base_node) # base_node (from arg) overrides YAML

        self.conf = merged
        super().__init__(merged)