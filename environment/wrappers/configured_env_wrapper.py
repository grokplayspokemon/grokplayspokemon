# grok_plays_pokemon/environment/wrappers/configured_env_wrapper.py
import yaml
from pathlib import Path
from omegaconf import OmegaConf, DictConfig
from .env_wrapper import EnvWrapper

class ConfiguredEnvWrapper(EnvWrapper):
    def __init__(self, base_conf, cli_args=None, config_path=None):
        # 1) load base defaults from provided dict or DictConfig
        if isinstance(base_conf, DictConfig):
            base_node = base_conf
        else:
            base_node = OmegaConf.create(base_conf)

        # 2) load YAML overrides
        cfg_file = config_path or (Path(__file__).parent.parent / "config.yaml")
        yaml_conf = {}
        if cfg_file.is_file():
            with open(cfg_file) as f:
                full = yaml.safe_load(f) or {}
            # separate top-level settings, env section, and env_config section
            root_conf = {k: v for k, v in full.items() if k not in ("env", "env_config")}
            env_section = full.get("env", {})
            env_config_section = full.get("env_config", {})
            # flatten root_conf, env_section, and env_config_section so all overrides are applied
            yaml_conf = {**root_conf, **env_section, **env_config_section}

        # 3) merge YAML defaults first, then apply any base_conf overrides (e.g., override_init_state) last
        merged = OmegaConf.merge(
            OmegaConf.create(yaml_conf),
            base_node
        )
        # Store merged config for external verification
        self.conf = merged
        # 4) hand merged DictConfig to super
        super().__init__(merged)