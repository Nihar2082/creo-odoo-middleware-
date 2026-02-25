#!/usr/bin/env python3
"""
Selective Database Cleanup Tool
Safely delete old test data from PostgreSQL backend with confirmations.
"""

import requests
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# Load API configuration
def load_api_config() -> dict:
    """Load API URL and key from config.json"""
    config_path = Path(__file__).resolve().parent / 'config.json'
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}

CONFIG = load_api_config()
API_URL = CONFIG.get('api_url', 'http://localhost:8000')
API_KEY = CONFIG.get('api_key', '')

SESSION = requests.Session()
if API_KEY:
    SESSION.headers.update({'X-API-Key': API_KEY})

def get_all_parts():
    """Fetch all parts from backend."""
    try:
        resp = SESSION.get(f"{API_URL}/parts", timeout=20)
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        print(f"❌ Error fetching parts: {e}")
        return []

def delete_part(external_id):
    """Delete a single part by external_id."""
    try:
        resp = SESSION.delete(f"{API_URL}/parts/{external_id}", timeout=20)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Error deleting {external_id}: {e}")
        return False

def show_parts_by_prefix(parts):
    """Show parts grouped by prefix."""
    by_prefix = defaultdict(list)
    for p in parts:
        ext_id = p.get("external_id", "")
        if "_" in ext_id:
            prefix = ext_id.split("_")[0]
            by_prefix[prefix].append(p)
    
    print("\n" + "=" * 80)
    print("CURRENT DATABASE STATE")
    print("=" * 80)
    
    for prefix in sorted(by_prefix.keys()):
        parts_list = by_prefix[prefix]
        print(f"\n[{prefix}] - {len(parts_list)} parts")
        
        # Extract numbers
        numbers = []
        for p in parts_list:
            ext_id = p.get("external_id", "")
            try:
                num = int(ext_id.split("_")[1])
                numbers.append(num)
            except:
                pass
        
        if numbers:
            print(f"  Range: {prefix}_{min(numbers):06d} to {prefix}_{max(numbers):06d}")
        
        # Show samples
        print(f"  Samples:")
        for p in parts_list[:2]:
            print(f"    - {p.get('external_id')}: {p.get('part_name', 'N/A')}")
        if len(parts_list) > 4:
            print(f"    ...")
        for p in parts_list[-2:]:
            print(f"    - {p.get('external_id')}: {p.get('part_name', 'N/A')}")
    
    return by_prefix

def filter_by_number_range(parts, prefix, start_num, end_num):
    """Filter parts by number range for a prefix."""
    filtered = []
    for p in parts:
        ext_id = p.get("external_id", "")
        if ext_id.startswith(f"{prefix}_"):
            try:
                num = int(ext_id.split("_")[1])
                if start_num <= num <= end_num:
                    filtered.append(p)
            except:
                pass
    return filtered

def filter_by_prefix(parts, prefix):
    """Get all parts with a specific prefix."""
    return [p for p in parts if p.get("external_id", "").startswith(f"{prefix}_")]

def main():
    print("\n" + "=" * 80)
    print("SELECTIVE DATABASE CLEANUP TOOL")
    print("=" * 80)
    print("\nWARNING: Deletions are PERMANENT and IRREVERSIBLE!")
    print("Make sure you understand what you're deleting before confirming.\n")
    
    # Show configuration
    print("Configuration:")
    print(f"  API URL: {API_URL}")
    print(f"  API Key: {'Loaded' if API_KEY else 'Not found'}")
    print()
    
    # Fetch current data
    parts = get_all_parts()
    if not parts:
        print("No parts found in database or unable to connect")
        print(f"\nTroubleshooting:")
        print(f"  1. Check config.json for correct api_url and api_key")
        print(f"  2. Ensure backend API is running at {API_URL}")
        print(f"  3. Run main app first to configure if needed")
        return
    
    by_prefix = show_parts_by_prefix(parts)
    
    print("\n" + "=" * 80)
    print("CLEANUP OPTIONS")
    print("=" * 80)
    print("""
1. Delete specific prefix range     (e.g., PS_000001 to PS_000100)
2. Delete entire prefix             (e.g., all PS_* parts)
3. Delete by external ID            (e.g., PS_000460)
4. View and choose individually    (interactive selection)
5. Cancel - don't delete anything
    """)
    
    choice = input("\nSelect option (1-5): ").strip()
    
    to_delete = []
    
    if choice == "1":
        prefix = input("Enter prefix (PS/STD/MD/etc.): ").strip().upper()
        if prefix not in by_prefix:
            print(f"Prefix '{prefix}' not found")
            return
        
        try:
            start = int(input(f"Start number (e.g., 460): "))
            end = int(input(f"End number (e.g., 466): "))
        except ValueError:
            print("Invalid number")
            return
    elif choice == "2":
        prefix = input("Enter prefix to delete entirely (PS/STD/MD/etc.): ").strip().upper()
        if prefix not in by_prefix:
            print(f"Prefix '{prefix}' not found")
            return
        
        to_delete = filter_by_prefix(parts, prefix)
        
    elif choice == "3":
        ext_id = input("Enter external ID (e.g., PS_000460): ").strip().upper()
        to_delete = [p for p in parts if p.get("external_id", "").upper() == ext_id]
        
        if not to_delete:
            print(f"External ID '{ext_id}' not found")
            return
    
    elif choice == "4":
        print("\n" + "=" * 80)
        print("INTERACTIVE SELECTION")
        print("=" * 80)
        
        for p in parts[:20]:  # Show first 20 for selection
            ext_id = p.get("external_id", "")
            name = p.get("part_name", "N/A")
            print(f"  {ext_id}: {name}")
        
        if len(parts) > 20:
            print(f"  ... and {len(parts) - 20} more parts")
        
        ext_id = input("\nEnter external ID to delete (or press Enter to cancel): ").strip().upper()
        if ext_id:
            to_delete = [p for p in parts if p.get("external_id", "").upper() == ext_id]
        
    elif choice == "5":
        print("Cancelled - no deletions made")
        return
    else:
        print("Invalid option")
        return
    
    if not to_delete:
        print("No parts matched your criteria")
        return
    
    # Show what will be deleted
    print("\n" + "=" * 80)
    print(f"PARTS TO BE DELETED ({len(to_delete)} total)")
    print("=" * 80)
    
    for p in to_delete:
        ext_id = p.get("external_id", "")
        name = p.get("part_name", "N/A")
        print(f"  - {ext_id}: {name}")
    
    # Final confirmation
    print("\nWARNING: THIS CANNOT BE UNDONE!")
    confirm = input(f"\nType 'DELETE' to confirm deletion of {len(to_delete)} part(s): ").strip()
    
    if confirm != "DELETE":
        print("Cancelled - no deletions made")
        return
    
    # Perform deletion
    print("\n" + "=" * 80)
    print("DELETING...")
    print("=" * 80)
    
    deleted = 0
    failed = 0
    
    for p in to_delete:
        ext_id = p.get("external_id", "")
        if delete_part(ext_id):
            print(f"Deleted: {ext_id}")
            deleted += 1
        else:
            print(f"Failed: {ext_id}")
            failed += 1
    
    print("\n" + "=" * 80)
    print(f"DELETION COMPLETE")
    print("=" * 80)
    print(f"  Deleted: {deleted} parts")
    print(f"  Failed: {failed} parts")
    print(f"  Total: {deleted + failed} processed")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user - no deletions made")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
