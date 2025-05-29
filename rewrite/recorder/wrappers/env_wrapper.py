from environment import RedGymEnv

class EnvWrapper(RedGymEnv):
    """
    A drop-in wrapper ensuring:
      1) Exactly one action per step()
      2) Final RAM/screen reads (including dialog) are freshly gathered
    """
    def step(self, action):
        # Execute original step logic
        raw_obs, reward, reset, done, info = super().step(action)

        # Freshly gather observation and dialog to avoid stale data
        fresh_obs = self._get_obs()
        fresh_dialog = self.read_dialog()

        # Attach dialog to observation if it's a dict
        if isinstance(fresh_obs, dict):
            fresh_obs['dialog'] = fresh_dialog

        # Also include dialog in info
        info['dialog'] = fresh_dialog

        return fresh_obs, reward, reset, done, info 