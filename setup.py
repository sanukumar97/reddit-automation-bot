#!/usr/bin/env python3
"""
Reddit Automation System - Setup Script
Run this ONCE to install everything and verify your setup.
Usage: python setup.py
"""

import os
import sys
import subprocess
import platform


def print_header():
    print("\n" + "=" * 60)
    print("  REDDIT AUTOMATION SYSTEM - SETUP")
    print("=" * 60 + "\n")


def print_step(num, total, msg):
    print(f"\n[{num}/{total}] {msg}")
    print("-" * 40)


def run_command(cmd, description=""):
    """Run a command and return True if successful."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True
        )
        if result.returncode == 0:
            return True
        else:
            print(f"  ⚠️  Warning: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def check_python_version():
    """Ensure Python 3.8+"""
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 8):
        print(f"  ❌ Python 3.8+ required (you have {major}.{minor})")
        print("  Download Python from: https://python.org")
        sys.exit(1)
    print(f"  ✅ Python {major}.{minor} — OK")


def install_packages():
    """Install required Python packages."""
    print("  Installing packages...")
    pip_cmd = "pip3" if platform.system() != "Windows" else "pip"

    packages = [
        "praw>=7.7.0",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "feedparser>=6.0.10",
        "lxml>=4.9.0",
        "flask>=3.0.0",
        "pyyaml>=6.0",
        "apscheduler>=3.10.0"
    ]

    for pkg in packages:
        print(f"  Installing {pkg}...")
        ok = run_command(f"{pip_cmd} install \"{pkg}\" -q")
        status = "✅" if ok else "⚠️ "
        print(f"  {status} {pkg}")


def initialize_database():
    """Create the SQLite database."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from modules.knowledge_base import initialize_database as init_db
        init_db()
        print("  ✅ Database created: database/knowledge_base.db")
    except Exception as e:
        print(f"  ❌ Database error: {e}")


def check_ollama():
    """Check if Ollama is installed."""
    result = run_command("ollama --version")
    if result:
        print("  ✅ Ollama is installed")
        return True
    else:
        print("  ⚠️  Ollama not found")
        print("  → Install from: https://ollama.ai")

        os_name = platform.system()
        if os_name == "Darwin":  # macOS
            print("  → macOS: brew install ollama  OR  download from ollama.ai")
        elif os_name == "Linux":
            print("  → Linux: curl -fsSL https://ollama.ai/install.sh | sh")
        elif os_name == "Windows":
            print("  → Windows: Download installer from https://ollama.ai")
        return False


def check_config():
    """Verify config files exist."""
    config_files = [
        "config/settings.yaml",
        "config/sources.yaml",
        "config/subreddits.yaml",
        "config/writing_instructions.md"
    ]
    all_ok = True
    for f in config_files:
        path = os.path.join(os.path.dirname(__file__), f)
        if os.path.exists(path):
            print(f"  ✅ {f}")
        else:
            print(f"  ❌ Missing: {f}")
            all_ok = False
    return all_ok


def print_next_steps():
    print("\n" + "=" * 60)
    print("  SETUP COMPLETE — NEXT STEPS")
    print("=" * 60)
    print("""
STEP 1: Install and start Ollama (free local AI)
  → Download from: https://ollama.ai
  → After installing, open Terminal and run:
      ollama serve
      ollama pull llama3.1

STEP 2: Get Reddit API credentials (free)
  → Go to: https://www.reddit.com/prefs/apps
  → Click "Create App"
  → Select type: "script"
  → Fill in name (anything), redirect URI: http://localhost:8080
  → Copy the client_id (under app name) and client_secret

STEP 3: Add your credentials to config/settings.yaml
  → Open the file and fill in:
      reddit.client_id: "paste here"
      reddit.client_secret: "paste here"
      reddit.username: "your reddit username"
      reddit.password: "your reddit password"

STEP 4: Add sources to config/sources.yaml
  → Replace the example URLs with your actual blog/RSS URLs

STEP 5: Run the bot!
  → Check status:     python main.py --status
  → Open dashboard:   python main.py --dashboard
    Then visit: http://localhost:5000
  → Run once:         python main.py --once
  → Run continuously: python main.py
""")


def main():
    print_header()
    total_steps = 5

    print_step(1, total_steps, "Checking Python version")
    check_python_version()

    print_step(2, total_steps, "Installing Python packages")
    install_packages()

    print_step(3, total_steps, "Initializing database")
    initialize_database()

    print_step(4, total_steps, "Checking Ollama (free local AI)")
    check_ollama()

    print_step(5, total_steps, "Verifying config files")
    check_config()

    print_next_steps()


if __name__ == '__main__':
    main()
