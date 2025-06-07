#!/usr/bin/env python3
"""
Installation script for Dear PyGui
Includes fallback handling for different environments
"""

import subprocess
import sys
import os

def install_dearpygui():
    """Install Dear PyGui with appropriate fallbacks"""
    try:
        print("Installing Dear PyGui...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "dearpygui"])
        print("Dear PyGui installed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to install Dear PyGui via pip: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error installing Dear PyGui: {e}")
        return False

def test_import():
    """Test if Dear PyGui can be imported"""
    try:
        import dearpygui.dearpygui as dpg
        print("Dear PyGui import test: SUCCESS")
        return True
    except ImportError as e:
        print(f"Dear PyGui import test: FAILED - {e}")
        return False

def main():
    print("Dear PyGui Setup Utility")
    print("=" * 40)
    
    # First check if it's already installed
    if test_import():
        print("Dear PyGui is already installed and working!")
        return
    
    # Try to install
    if install_dearpygui():
        if test_import():
            print("Installation and test successful!")
        else:
            print("Installation completed but import test failed.")
            print("You may need to restart your Python environment.")
    else:
        print("Installation failed. Please install manually:")
        print("pip install dearpygui")

if __name__ == "__main__":
    main() 