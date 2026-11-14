# Laptop-Server Security Guide

This document provides a operational security checklist for running the search engine API server on a personal or development Windows laptop.

## 1. Loopback-Only by Default (The Golden Rule)

By default, the server binds exclusively to `127.0.0.1:8000`. In this configuration:
- Only local processes on the laptop can reach the API.
- Remote devices on your local Wi-Fi / LAN, or public internet, cannot reach the port.
- Any attempt to configure a non-loopback host (e.g. `0.0.0.0`, local network IP) **requires an explicit API key**, or the server refuses to start per security policy (D15).

## 2. Windows Defender Firewall Configuration

1. **Inbound Rules:**
   - Under normal operation (Release A), **no inbound firewall rule is needed or allowed**.
   - Keep Windows Defender Firewall enabled for Domain, Private, and Public profiles.
   - If Windows prompts: *"Windows Defender Firewall has blocked some features of Python"*, choose **Cancel** unless Step 28 (remote cluster testing) has been explicitly approved by the human operator.
2. **Port Forwarding:**
   - Never configure router port forwarding (NAT / UPnP) pointing to port 8000 on your laptop.
   - The search engine is designed for local, self-hosted, sovereign search.

## 3. API Key Management & Rotation

When accessing the server across isolated boundaries or requiring authentication:
1. **Generating Keys:**
   ```powershell
   search-engine auth create-key --name "my-laptop-client"
   ```
   Keys are 256-bit URL-safe tokens with the `se_` prefix (e.g. `se_abc123...`).
2. **Key Storage:**
   - Keys are stored in the database **strictly as SHA-256 hashes**.
   - The plaintext key is printed only once upon creation.
   - Never commit API keys to version control (`.gitignore` protects configuration).
3. **Transmission:**
   - Send keys exclusively in the HTTP header: `Authorization: Bearer <key>`.
   - Never put keys in URL query strings or path parameters.
4. **Key Rotation:**
   - Revoke compromised or retired keys immediately:
     ```powershell
     search-engine auth list-keys
     search-engine auth revoke-key <key_id>
     ```

## 4. Operational Best Practices

- **Stop Server When Idle:** When not using search or crawling, terminate the server (`Ctrl+C` or kill process) rather than leaving long-lived daemons running unattended.
- **Untrusted / Public Wi-Fi:** When on public or untrusted Wi-Fi networks (cafes, airports, hotels), ensure the server is either stopped or verified bound to `127.0.0.1`.
- **Dependency Auditing:** Regularly run `scripts/audit.ps1` (or `bandit` and `pip-audit`) to detect vulnerabilities in dependencies.
- **Data Protection:** The `data/` directory contains your indexed documents and cache. Ensure restrictive Windows file permissions (ACLs) so other laptop users cannot read local database files.
