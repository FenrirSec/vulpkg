#!/usr/bin/env python3
"""
VulPKG - Vulpes Package Manager
A simple package manager for Vulpes OS that handles custom application installations
"""

import os
import sys
import json
import subprocess
import argparse
from pathlib import Path
from typing import Dict, List, Optional
import shutil

# Constants
PACKAGE_REPO = Path("/var/lib/vulpkg/repo")  # Repository for .vulpkg files
INSTALLED_DB = Path("/var/lib/vulpkg/installed.json")
DEFAULT_INSTALL_DIR = Path("/opt/vulpkg")

class VulPKG:
    def __init__(self):
        self.package_dir = PACKAGE_DIR
        self.installed_db = INSTALLED_DB
        self.install_dir = DEFAULT_INSTALL_DIR
        
        # Ensure directories exist
        self.package_dir.mkdir(parents=True, exist_ok=True)
        self.installed_db.parent.mkdir(parents=True, exist_ok=True)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        
        # Load installed packages database
        self.installed = self._load_installed()
    
    def _load_installed(self) -> Dict:
        """Load the installed packages database"""
        if self.installed_db.exists():
            try:
                with open(self.installed_db, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}
    
    def _save_installed(self):
        """Save the installed packages database"""
        with open(self.installed_db, 'w') as f:
            json.dump(self.installed, f, indent=2)
    
    def _run_command(self, command: str, shell: bool = True, sudo: bool = False) -> tuple:
        """Run a shell command and return (success, output)"""
        if sudo and os.geteuid() != 0:
            command = f"sudo {command}"
        
        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=True,
                text=True,
                check=True
            )
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            return False, e.stderr
    
    def _install_alpine_packages(self, packages: List[str], sudo: bool = False) -> bool:
        """Install Alpine packages using apk"""
        if not packages:
            return True
        
        print(f"[*] Installing Alpine packages: {', '.join(packages)}")
        
        for pkg in packages:
            # Check if it's an edge package
            if pkg.startswith("edge:"):
                pkg_name = pkg.replace("edge:", "")
                cmd = f"apk add --repository=http://dl-cdn.alpinelinux.org/alpine/edge/community {pkg_name}"
            else:
                cmd = f"apk add {pkg}"
            
            success, output = self._run_command(cmd, sudo=sudo)
            if not success:
                print(f"[!] Failed to install {pkg}: {output}")
                return False
            print(f"[+] Installed {pkg}")
        
        return True
    
    def _execute_script(self, script: str, working_dir: Path, sudo: bool = False) -> bool:
        """Execute an installation script"""
        if not script.strip():
            return True
        
        print("[*] Running installation script...")
        
        # Create a temporary script file
        script_file = working_dir / "install_script.sh"
        with open(script_file, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write(f"cd {working_dir}\n")
            f.write(script)
        
        script_file.chmod(0o755)
        
        # Run the script
        cmd = str(script_file)
        success, output = self._run_command(cmd, sudo=sudo)
        
        # Clean up
        script_file.unlink()
        
        if not success:
            print(f"[!] Installation script failed: {output}")
            return False
        
        print("[+] Installation script completed")
        return True
    
    def _create_files(self, files: Dict[str, str], working_dir: Path, sudo: bool = False) -> bool:
        """Create final files from package definition"""
        if not files:
            return True
        
        print("[*] Creating package files...")
        
        for filepath, content in files.items():
            target = Path(filepath)
            
            # Handle relative paths
            if not target.is_absolute():
                target = working_dir / target
            
            # Create parent directories
            target.parent.mkdir(parents=True, exist_ok=True)
            
            # Write file
            try:
                with open(target, 'w') as f:
                    f.write(content)
                
                # Make executable if it's a script
                if filepath.endswith('.sh'):
                    target.chmod(0o755)
                
                print(f"[+] Created {target}")
            except PermissionError:
                if sudo:
                    # Use sudo to write the file
                    temp_file = working_dir / "temp_file"
                    with open(temp_file, 'w') as f:
                        f.write(content)
                    self._run_command(f"cp {temp_file} {target}", sudo=True)
                    temp_file.unlink()
                    if filepath.endswith('.sh'):
                        self._run_command(f"chmod +x {target}", sudo=True)
                    print(f"[+] Created {target} (with sudo)")
                else:
                    print(f"[!] Permission denied: {target}")
                    return False
        
        return True
    
    def install(self, package_file: Path) -> bool:
        """Install a package from a .vulpkg file"""
        if not package_file.exists():
            print(f"[!] Package file not found: {package_file}")
            return False
        
        # Load package definition
        try:
            with open(package_file, 'r') as f:
                pkg_def = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[!] Invalid package file: {e}")
            return False
        
        # Validate package definition
        required_fields = ['name', 'version']
        for field in required_fields:
            if field not in pkg_def:
                print(f"[!] Missing required field: {field}")
                return False
        
        pkg_name = pkg_def['name']
        pkg_version = pkg_def['version']
        
        print(f"\n[VULPKG] Installing {pkg_name} v{pkg_version}")
        print("=" * 50)
        
        # Check if already installed
        if pkg_name in self.installed:
            print(f"[!] {pkg_name} is already installed (v{self.installed[pkg_name]['version']})")
            response = input("Do you want to reinstall? [y/N]: ")
            if response.lower() != 'y':
                return False
            self.remove(pkg_name)
        
        # Create package working directory
        pkg_install_dir = self.install_dir / pkg_name
        pkg_install_dir.mkdir(parents=True, exist_ok=True)
        
        # Get installation parameters
        requires_sudo = pkg_def.get('requires_sudo', False)
        alpine_packages = pkg_def.get('alpine_packages', [])
        install_script = pkg_def.get('install_script', '')
        files = pkg_def.get('files', {})
        
        # Check for sudo requirement
        if requires_sudo and os.geteuid() != 0:
            print("[!] This package requires sudo privileges")
        
        # Install Alpine packages
        if alpine_packages:
            if not self._install_alpine_packages(alpine_packages, sudo=requires_sudo):
                print(f"[!] Failed to install Alpine packages for {pkg_name}")
                return False
        
        # Execute installation script
        if install_script:
            if not self._execute_script(install_script, pkg_install_dir, sudo=requires_sudo):
                print(f"[!] Installation script failed for {pkg_name}")
                return False
        
        # Create final files
        if files:
            if not self._create_files(files, pkg_install_dir, sudo=requires_sudo):
                print(f"[!] Failed to create files for {pkg_name}")
                return False
        
        # Record installation
        self.installed[pkg_name] = {
            'version': pkg_version,
            'install_dir': str(pkg_install_dir),
            'requires_sudo': requires_sudo,
            'description': pkg_def.get('description', '')
        }
        self._save_installed()
        
        print(f"\n[+] Successfully installed {pkg_name} v{pkg_version}")
        return True
    
    def remove(self, package_name: str) -> bool:
        """Remove an installed package"""
        if package_name not in self.installed:
            print(f"[!] Package not installed: {package_name}")
            return False
        
        pkg_info = self.installed[package_name]
        install_dir = Path(pkg_info['install_dir'])
        
        print(f"[*] Removing {package_name} v{pkg_info['version']}")
        
        # Remove installation directory
        if install_dir.exists():
            shutil.rmtree(install_dir)
            print(f"[+] Removed {install_dir}")
        
        # Remove from database
        del self.installed[package_name]
        self._save_installed()
        
        print(f"[+] Successfully removed {package_name}")
        return True
    
    def list_installed(self):
        """List all installed packages"""
        if not self.installed:
            print("No packages installed.")
            return
        
        print("\n[VULPKG] Installed Packages")
        print("=" * 50)
        for name, info in self.installed.items():
            print(f"  {name} (v{info['version']})")
            if info.get('description'):
                print(f"    {info['description']}")
            print(f"    Location: {info['install_dir']}")
            print()
    
    def info(self, package_name: str):
        """Show information about an installed package"""
        if package_name not in self.installed:
            print(f"[!] Package not installed: {package_name}")
            return
        
        info = self.installed[package_name]
        print(f"\n[VULPKG] Package: {package_name}")
        print("=" * 50)
        print(f"Version: {info['version']}")
        print(f"Description: {info.get('description', 'N/A')}")
        print(f"Install Directory: {info['install_dir']}")
        print(f"Requires Sudo: {info['requires_sudo']}")


def main():
    parser = argparse.ArgumentParser(
        description='VulPKG - Vulpes Package Manager',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Install command
    install_parser = subparsers.add_parser('install', help='Install a package')
    install_parser.add_argument('package', type=Path, help='Path to .vulpkg file')
    
    # Remove command
    remove_parser = subparsers.add_parser('remove', help='Remove a package')
    remove_parser.add_argument('package', help='Package name to remove')
    
    # List command
    subparsers.add_parser('list', help='List installed packages')
    
    # Info command
    info_parser = subparsers.add_parser('info', help='Show package information')
    info_parser.add_argument('package', help='Package name')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    manager = VulPKG()
    
    if args.command == 'install':
        manager.install(args.package)
    elif args.command == 'remove':
        manager.remove(args.package)
    elif args.command == 'list':
        manager.list_installed()
    elif args.command == 'info':
        manager.info(args.package)


if __name__ == '__main__':
    main()
