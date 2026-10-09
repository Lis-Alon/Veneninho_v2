"""Memory Bank for Veneno do Pokemon Bot

This module provides persistent state management and memory storage for the bot.
It tracks game state, captured Pokémon, positions, and other relevant data across runs.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


class MemoryBank:
    """Manages persistent memory for the Pokémon bot.
    
    Stores:
    - Game state (current area, status)
    - Captured Pokémon history
    - Icon positions and timestamps
    - Bot configuration and settings
    """
    
    def __init__(self, storage_path=None):
        """Initialize the MemoryBank.
        
        Args:
            storage_path: Path to the storage file/database. 
                          Defaults to <project>/memory_bank.db
        """
        if storage_path is None:
            project_dir = Path(__file__).parent if '__file__' in dir() else Path.cwd()
            storage_path = project_dir / "memory_bank.db"
        
        self.storage_path = Path(storage_path)
        self._init_db()
    
    def _init_db(self):
        """Initialize the SQLite database with required tables."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            
            # Table: game_state - current bot state
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS game_state (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    current_area TEXT,
                    status TEXT,
                    last_update TIMESTAMP,
                    coordinates TEXT
                )
            """)
            
            # Table: captured_pokemon - history of captured Pokémon
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS captured_pokemon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pokemon_name TEXT,
                    image_path TEXT,
                    capture_time TIMESTAMP,
                    location TEXT,
                    rarity TEXT
                )
            """)
            
            # Table: icon_positions - tracked icon positions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS icon_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icon_name TEXT,
                    last_seen TIMESTAMP,
                    x_coordinate INTEGER,
                    y_coordinate INTEGER,
                    confidence REAL
                )
            """)
            
            # Insert initial stats if not exists
            cursor.execute("""
                INSERT OR IGNORE INTO bot_stats (id, uptime_start) 
                VALUES (1, datetime('now'))
            """)
            
            conn.commit()

    def update_game_state(self, area=None, status=None, coordinates=None):
        """Update the current game state.
        
        Args:
            area: Current map area/zone name
            status: Bot status (exploring, battling, capturing, menu)
            coordinates: Dict of relevant coordinates (optional)
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            
            # Get existing coordinates if updating
            if coordinates is None:
                cursor.execute("SELECT coordinates FROM game_state WHERE id = 1")
                row = cursor.fetchone()
                if row and row[0]:
                    coordinates = json.loads(row[0])
                else:
                    coordinates = {}
            
            # Merge with existing coordinates
            if coordinates:
                cursor.execute("SELECT coordinates FROM game_state WHERE id = 1")
                row = cursor.fetchone()
                if row and row[0]:
                    existing = json.loads(row[0])
                    existing.update(coordinates)
                    coordinates = existing
            
            cursor.execute("""
                INSERT INTO game_state (id, current_area, status, last_update, coordinates)
                VALUES (1, ?, ?, datetime('now'), ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_area = excluded.current_area,
                    status = excluded.status,
                    last_update = excluded.last_update,
                    coordinates = excluded.coordinates
            """, (area, status, json.dumps(coordinates) if coordinates else None))
            
            conn.commit()
    
    def get_game_state(self):
        """Retrieve the current game state.
        
        Returns:
            Dict with keys: area, status, coordinates, last_update
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_area, status, coordinates, last_update FROM game_state WHERE id = 1")
            row = cursor.fetchone()
            
            if not row:
                return {"area": None, "status": None, "coordinates": {}, "last_update": None}
            
            coordinates = {}
            if row[2]:
                try:
                    coordinates = json.loads(row[2])
                except json.JSONDecodeError:
                    coordinates = {}
            
            return {
                "area": row[0],
                "status": row[1],
                "coordinates": coordinates,
                "last_update": row[3]
            }
    
    def record_capture(self, pokemon_name, image_path, location=None, rarity="unknown"):
        """Record a captured Pokémon in the memory bank.
        
        Args:
            pokemon_name: Name of the captured Pokémon
            image_path: Path to the capture image
            location: Dict with x, y coordinates (optional)
            rarity: Rarity classification (common, uncommon, rare, legendary)
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO captured_pokemon (pokemon_name, image_path, capture_time, location, rarity)
                VALUES (?, ?, datetime('now'), ?, ?)
            """, (pokemon_name, image_path, json.dumps(location) if location else None, rarity))
            
            conn.commit()
def get_capture_history(self, limit=50, rarity=None):
        """Get history of captured Pokémon.

        Args:
            limit: Maximum number of records to return
            rarity: Filter by rarity (optional)

        Returns:
            List of dicts with capture records
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()

            if rarity:
                cursor.execute("""
                    SELECT pokemon_name, capture_time, location, rarity 
                    FROM captured_pokemon 
                    WHERE rarity = ?
                    ORDER BY capture_time DESC
                    LIMIT ?
                """, (rarity, limit))
            else:
                cursor.execute("""
                    SELECT pokemon_name, capture_time, location, rarity 
                    FROM captured_pokemon 
                    ORDER BY capture_time DESC
def increment_run_count(self):
        """Increment the total run count and update stats."""
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()

            # Get current stats
            cursor.execute("SELECT total_runs, total_captures, total_battles FROM bot_stats WHERE id = 1")
            row = cursor.fetchone()

            total_runs = (row[0] or 0) + 1
# Create default instance for convenience
_memory_bank = None

def get_memory_bank():
    """Get the default MemoryBank instance (lazy initialization)."""
    global _memory_bank
    if _memory_bank is None:
        _memory_bank = MemoryBank()
    return _memory_bank


# Convenience functions for common operations
def record_capture(pokemon_name, image_path, location=None, rarity="unknown"):
    """Quick function to record a capture."""
    mb = get_memory_bank()
    mb.record_capture(pokemon_name, image_path, location, rarity)


def update_game_state(area=None, status=None, coordinates=None):
    """Quick function to update game state."""
    mb = get_memory_bank()
    mb.update_game_state(area, status, coordinates)


def get_game_state():
    """Quick function to get game state."""
    mb = get_memory_bank()
    return mb.get_game_state()


def update_icon_position(icon_name, x, y, confidence=1.0):
    """Quick function to update icon position."""
    mb = get_memory_bank()
    mb.update_icon_position(icon_name, x, y, confidence)


def get_icon_position(icon_name):
    """Quick function to get icon position."""
    mb = get_memory_bank()
    return mb.get_icon_position(icon_name)


def get_stats():
    """Quick function to get bot statistics."""
    mb = get_memory_bank()
    return mb.get_stats()

            cursor.execute("""
                UPDATE bot_stats 
                SET total_runs = ?, 
                    last_session_duration = CASE WHEN ? > 0 THEN ? ELSE 0 END
                WHERE id = 1
            """, (total_runs, total_runs, total_runs))

            conn.commit()

    def get_stats(self):
        """Get bot statistics.

        Returns:
            Dict with run counts, captures, battles, and uptime info
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT total_runs, total_captures, total_battles, uptime_start 
                FROM bot_stats 
                WHERE id = 1
            """)
            row = cursor.fetchone()

            if not row:
                return {
                    "total_runs": 0,
                    "total_captures": 0,
                    "total_battles": 0,
                    "uptime_start": None
                }

            # Calculate session duration
            uptime_start = row[3]
            duration = None
            if uptime_start:
                try:
                    from datetime import datetime
                    start = datetime.strptime(str(uptime_start), "%Y-%m-%d %H:%M:%S.%f")
                    duration = (datetime.now() - start).total_seconds()
                except (ValueError, TypeError):
                    duration = None

            return {
                "total_runs": row[0] or 0,
                "total_captures": row[1] or 0,
                "total_battles": row[2] or 0,
                "uptime_start": str(uptime_start) if uptime_start else None,
                "session_duration_seconds": round(duration, 2) if duration else None
            }

    def save_config(self, config_dict, config_name="general"):
        """Save configuration to the memory bank.

        Args:
            config_dict: Dictionary of configuration values
            config_name: Name/key for the configuration section
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS configs (
                    config_name TEXT PRIMARY KEY,
                    config_data TEXT,
                    last_updated TIMESTAMP
                )
            """)

            cursor.execute("""
                INSERT OR REPLACE INTO configs (config_name, config_data, last_updated)
                VALUES (?, ?, datetime('now'))
            """, (config_name, json.dumps(config_dict)))

            conn.commit()

    def load_config(self, config_name="general"):
        """Load configuration from the memory bank.

        Args:
            config_name: Name/key of the configuration section

        Returns:
            Dict of configuration values, or empty dict if not found
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT config_data FROM configs WHERE config_name = ?
            """, (config_name,))
            row = cursor.fetchone()

            if not row:
                return {}

            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}

    def __del__(self):
        """Close database connections on deletion."""
        pass
                    LIMIT ?
                """, (limit,))

            return [
                {
                    "name": row[0],
                    "time": row[1],
                    "location": json.loads(row[2]) if row[2] else None,
                    "rarity": row[3]
                }
                for row in cursor.fetchall()
            ]

def update_icon_position(self, icon_name, x, y, confidence=1.0):
        """Update the last known position of an icon.

        Args:
            icon_name: Name/identifier of the icon
            x: X coordinate
            y: Y coordinate
            confidence: Detection confidence (0.0 to 1.0)
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO icon_positions (icon_name, last_seen, x_coordinate, y_coordinate, confidence)
                VALUES (?, datetime('now'), ?, ?, ?)
                ON CONFLICT(icon_name) DO UPDATE SET
                    last_seen = datetime('now'),
                    x_coordinate = excluded.x_coordinate,
                    y_coordinate = excluded.y_coordinate,
                    confidence = excluded.confidence
            """, (icon_name, x, y, confidence))

            conn.commit()

def get_icon_position(self, icon_name):
        """Get the last known position of an icon.

        Args:
            icon_name: Name/identifier of the icon

        Returns:
            Dict with x, y, confidence, last_seen or None
        """
        with sqlite3.connect(str(self.storage_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT x_coordinate, y_coordinate, confidence, last_seen 
                FROM icon_positions 
                WHERE icon_name = ?
            """, (icon_name,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                "x": row[0],
                "y": row[1],
                "confidence": row[2],
                "last_seen": row[3]
            }
