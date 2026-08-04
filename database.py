# database.py
import asyncpg
import os
import logging
from datetime import datetime, timedelta
import random
import string
import re
import asyncio

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
        self._lock = asyncio.Lock()
    
    async def connect_with_retry(self, max_retries=5, delay=3):
        for attempt in range(max_retries):
            try:
                database_url = os.getenv('DATABASE_URL')
                if not database_url:
                    raise ValueError("DATABASE_URL not found in environment variables")
                
                logger.info(f"Attempting database connection (attempt {attempt + 1}/{max_retries})")
                
                self.pool = await asyncpg.create_pool(
                    database_url,
                    min_size=1,
                    max_size=10,
                    command_timeout=60
                )
                
                async with self.pool.acquire() as conn:
                    await conn.execute("SELECT 1")
                    await self.create_tables(conn)
                    await self.create_indexes(conn)
                    await self.create_auto_lobbies(conn)
                
                logger.info("Database initialized successfully")
                return
                
            except Exception as e:
                logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    delay *= 1.5
                else:
                    logger.error(f"All database connection attempts failed: {e}")
                    raise
    
    async def create_tables(self, conn):
        try:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS players (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    nickname TEXT UNIQUE,
                    game_id TEXT UNIQUE,
                    registration_date TEXT
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS player_stats (
                    user_id BIGINT PRIMARY KEY REFERENCES players(user_id),
                    rating INTEGER DEFAULT 0,
                    matches_played INTEGER DEFAULT 0,
                    kills INTEGER DEFAULT 0,
                    deaths INTEGER DEFAULT 0,
                    assists INTEGER DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    CONSTRAINT rating_non_negative CHECK (rating >= 0),
                    CONSTRAINT matches_non_negative CHECK (matches_played >= 0),
                    CONSTRAINT kills_non_negative CHECK (kills >= 0),
                    CONSTRAINT deaths_non_negative CHECK (deaths >= 0),
                    CONSTRAINT assists_non_negative CHECK (assists >= 0),
                    CONSTRAINT points_non_negative CHECK (points >= 0),
                    CONSTRAINT wins_non_negative CHECK (wins >= 0),
                    CONSTRAINT losses_non_negative CHECK (losses >= 0)
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS lobbies (
                    lobby_id SERIAL PRIMARY KEY,
                    lobby_unique_id TEXT UNIQUE,
                    status TEXT DEFAULT 'waiting',
                    max_players INTEGER DEFAULT 10,
                    current_players INTEGER DEFAULT 0,
                    created_at TEXT,
                    match_confirmed BOOLEAN DEFAULT FALSE,
                    match_id TEXT UNIQUE,
                    map_name TEXT,
                    ct_captain_id BIGINT REFERENCES players(user_id),
                    t_captain_id BIGINT REFERENCES players(user_id),
                    banned_maps TEXT[] DEFAULT '{}',
                    picked_map TEXT,
                    match_score_ct INTEGER DEFAULT 0,
                    match_score_t INTEGER DEFAULT 0,
                    topic_thread_id INTEGER,
                    current_pick INTEGER DEFAULT 0,
                    current_ban INTEGER DEFAULT 0,
                    CONSTRAINT max_players_positive CHECK (max_players > 0),
                    CONSTRAINT current_players_non_negative CHECK (current_players >= 0),
                    CONSTRAINT valid_status CHECK (status IN ('waiting', 'picking', 'banning', 'ready', 'completed', 'cancelled'))
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS lobby_players (
                    id SERIAL PRIMARY KEY,
                    lobby_id INTEGER REFERENCES lobbies(lobby_id) ON DELETE CASCADE,
                    user_id BIGINT REFERENCES players(user_id),
                    joined_at TEXT,
                    match_confirmed BOOLEAN DEFAULT FALSE,
                    team TEXT,
                    pick_order INTEGER,
                    UNIQUE(lobby_id, user_id)
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    lobby_id INTEGER REFERENCES lobbies(lobby_id),
                    map_name TEXT,
                    ct_captain_id BIGINT REFERENCES players(user_id),
                    t_captain_id BIGINT REFERENCES players(user_id),
                    score_ct INTEGER DEFAULT 0,
                    score_t INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at TEXT,
                    completed_at TEXT,
                    CONSTRAINT valid_status CHECK (status IN ('active', 'completed', 'cancelled'))
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS match_players (
                    id SERIAL PRIMARY KEY,
                    match_id TEXT REFERENCES matches(match_id) ON DELETE CASCADE,
                    user_id BIGINT REFERENCES players(user_id),
                    team TEXT,
                    kills INTEGER DEFAULT 0,
                    deaths INTEGER DEFAULT 0,
                    assists INTEGER DEFAULT 0,
                    points INTEGER DEFAULT 0,
                    win BOOLEAN DEFAULT FALSE,
                    elo_change INTEGER DEFAULT 0,
                    UNIQUE(match_id, user_id),
                    CONSTRAINT valid_team CHECK (team IN ('CT', 'T'))
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS screenshots (
                    screenshot_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES players(user_id),
                    match_id TEXT REFERENCES matches(match_id) ON DELETE CASCADE,
                    topic_thread_id INTEGER,
                    created_at TEXT,
                    status TEXT DEFAULT 'pending',
                    CONSTRAINT valid_screenshot_status CHECK (status IN ('pending', 'processed', 'rejected'))
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS stats_history (
                    history_id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES players(user_id),
                    match_id TEXT REFERENCES matches(match_id) ON DELETE CASCADE,
                    kills_added INTEGER,
                    deaths_added INTEGER,
                    assists_added INTEGER,
                    points_added INTEGER,
                    elo_change INTEGER,
                    win_added BOOLEAN,
                    created_at TEXT,
                    UNIQUE(user_id, match_id),
                    CONSTRAINT history_kills_non_negative CHECK (kills_added >= 0),
                    CONSTRAINT history_deaths_non_negative CHECK (deaths_added >= 0),
                    CONSTRAINT history_assists_non_negative CHECK (assists_added >= 0),
                    CONSTRAINT history_points_non_negative CHECK (points_added >= 0)
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS top_players (
                    user_id BIGINT PRIMARY KEY REFERENCES players(user_id),
                    nickname TEXT NOT NULL,
                    rating INTEGER NOT NULL DEFAULT 0,
                    matches_played INTEGER NOT NULL DEFAULT 0,
                    kills INTEGER NOT NULL DEFAULT 0,
                    deaths INTEGER NOT NULL DEFAULT 0,
                    assists INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    kd_ratio DECIMAL(5,2) NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            logger.info("Tables created successfully")
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            raise
    
    async def create_indexes(self, conn):
        try:
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_player_stats_rating ON player_stats(rating)",
                "CREATE INDEX IF NOT EXISTS idx_player_stats_matches ON player_stats(matches_played)",
                "CREATE INDEX IF NOT EXISTS idx_lobbies_status ON lobbies(status)",
                "CREATE INDEX IF NOT EXISTS idx_lobbies_created ON lobbies(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_lobby_players_user_id ON lobby_players(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_lobby_players_lobby_id ON lobby_players(lobby_id)",
                "CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)",
                "CREATE INDEX IF NOT EXISTS idx_match_players_match_id ON match_players(match_id)",
                "CREATE INDEX IF NOT EXISTS idx_match_players_user_id ON match_players(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_screenshots_match_id ON screenshots(match_id)",
                "CREATE INDEX IF NOT EXISTS idx_screenshots_status ON screenshots(status)",
                "CREATE INDEX IF NOT EXISTS idx_stats_history_user_match ON stats_history(user_id, match_id)",
                "CREATE INDEX IF NOT EXISTS idx_players_nickname ON players(nickname)",
                "CREATE INDEX IF NOT EXISTS idx_players_game_id ON players(game_id)",
                "CREATE INDEX IF NOT EXISTS idx_lobbies_unique_id ON lobbies(lobby_unique_id)",
                "CREATE INDEX IF NOT EXISTS idx_lobbies_match_id ON lobbies(match_id)",
                "CREATE INDEX IF NOT EXISTS idx_lobbies_topic_thread ON lobbies(topic_thread_id)",
                "CREATE INDEX IF NOT EXISTS idx_top_players_rating ON top_players(rating DESC)",
                "CREATE INDEX IF NOT EXISTS idx_top_players_position ON top_players(position)",
                "CREATE INDEX IF NOT EXISTS idx_top_players_updated ON top_players(last_updated)"
            ]
            
            for index_sql in indexes:
                await conn.execute(index_sql)
            
            logger.info("Indexes created successfully")
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")
    
    async def create_auto_lobbies(self, conn):
        try:
            count = await conn.fetchval("SELECT COUNT(*) FROM lobbies WHERE status = 'waiting'")
            
            if count == 0:
                for i in range(1, 6):
                    lobby_unique_id = str(i)
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    await conn.execute('''
                        INSERT INTO lobbies (lobby_unique_id, created_at, status, max_players)
                        VALUES ($1, $2, 'waiting', $3)
                    ''', lobby_unique_id, created_at, 10)
                
                logger.info("5 automatic lobbies created for 10 players (5x5 only)")
        except Exception as e:
            logger.error(f"Error creating auto lobbies: {e}")

    async def create_new_lobby(self):
        try:
            async with self.pool.acquire() as conn:
                available_lobby_number = await conn.fetchval('''
                    SELECT MIN(CAST(lp.lobby_unique_id AS INTEGER))
                    FROM (
                        SELECT lobby_unique_id 
                        FROM lobbies 
                        WHERE status IN ('completed', 'cancelled')
                        AND lobby_unique_id ~ '^[0-9]+$'
                        AND CAST(lobby_unique_id AS INTEGER) NOT IN (
                            SELECT CAST(lobby_unique_id AS INTEGER) 
                            FROM lobbies 
                            WHERE status IN ('waiting', 'picking', 'banning', 'ready')
                            AND lobby_unique_id ~ '^[0-9]+$'
                        )
                    ) AS lp
                ''')
                
                if available_lobby_number:
                    lobby_unique_id = str(available_lobby_number)
                    logger.info(f"Reusing lobby number {lobby_unique_id} from completed/cancelled lobby")
                else:
                    max_lobby_number = await conn.fetchval("""
                        SELECT MAX(CAST(lobby_unique_id AS INTEGER)) 
                        FROM lobbies 
                        WHERE lobby_unique_id ~ '^[0-9]+$'
                    """) or 0
                    new_lobby_number = max_lobby_number + 1
                    lobby_unique_id = str(new_lobby_number)
                    logger.info(f"Creating new lobby number {lobby_unique_id}")
                
                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                max_players = 10
                
                await conn.execute('''
                    INSERT INTO lobbies (lobby_unique_id, created_at, status, max_players)
                    VALUES ($1, $2, 'waiting', $3)
                ''', lobby_unique_id, created_at, max_players)
                
                logger.info(f"New lobby #{lobby_unique_id} created automatically for {max_players} players (5x5)")
                return True
        except Exception as e:
            logger.error(f"Error creating new lobby: {e}")
            return False
    
    async def is_nickname_taken(self, nickname):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1 FROM players WHERE nickname = $1", nickname)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking nickname {nickname}: {e}")
            return True

    async def is_game_id_taken(self, game_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1 FROM players WHERE game_id = $1", game_id)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking game_id {game_id}: {e}")
            return True

    async def is_user_registered(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1 FROM players WHERE user_id = $1", user_id)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking user registration {user_id}: {e}")
            return False

    async def register_player(self, user_id, username, nickname, game_id):
        if not re.match(r'^[a-zA-Z0-9_]{3,16}$', nickname):
            return False, "Никнейм должен содержать только английские буквы, цифры и подчеркивания (3-16 символов)"
        
        if not re.match(r'^\d{8,13}$', game_id):
            return False, "Игровой ID должен содержать только цифры (8-13 символов)"
        
        if await self.is_nickname_taken(nickname):
            return False, "Этот никнейм уже занят"
        
        if await self.is_game_id_taken(game_id):
            return False, "Этот игровой ID уже занят"
        
        registration_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO players (user_id, username, nickname, game_id, registration_date) VALUES ($1, $2, $3, $4, $5)",
                        user_id, username, nickname, game_id, registration_date
                    )
                    await conn.execute(
                        "INSERT INTO player_stats (user_id) VALUES ($1)",
                        user_id
                    )
            
            logger.info(f"User {user_id} registered successfully with nickname {nickname} and game_id {game_id}")
            return True, "Успешная регистрация"
        except Exception as e:
            logger.error(f"Error in register_player for user {user_id}: {e}")
            return False, f"Ошибка при регистрации: {str(e)}"

    async def get_player_profile(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchrow('''
                    SELECT p.user_id, p.username, p.nickname, p.game_id, p.registration_date,
                           ps.rating, ps.matches_played, ps.kills, ps.deaths, ps.assists, 
                           ps.points, ps.wins, ps.losses
                    FROM players p
                    LEFT JOIN player_stats ps ON p.user_id = ps.user_id
                    WHERE p.user_id = $1
                ''', user_id)
        except Exception as e:
            logger.error(f"Error getting player profile for {user_id}: {e}")
            return None
    
    async def get_player_by_id(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchrow("SELECT nickname, game_id FROM players WHERE user_id = $1", user_id)
        except Exception as e:
            logger.error(f"Error getting player by ID {user_id}: {e}")
            return None

    async def get_player_rating(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT rating FROM player_stats WHERE user_id = $1', user_id)
                return result if result else 0
        except Exception as e:
            logger.error(f"Error getting player rating for {user_id}: {e}")
            return 0

    async def get_available_lobbies(self):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetch('''
                    SELECT lobby_id, lobby_unique_id, current_players, max_players, status
                    FROM lobbies 
                    WHERE status = 'waiting' AND current_players < max_players
                    ORDER BY CAST(lobby_unique_id AS INTEGER)
                ''')
        except Exception as e:
            logger.error(f"Error getting available lobbies: {e}")
            return []

    async def get_lobby_by_id(self, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchrow('''
                    SELECT lobby_id, lobby_unique_id, status, max_players, current_players, 
                           match_confirmed, match_id, map_name, ct_captain_id, t_captain_id,
                           banned_maps, picked_map, match_score_ct, match_score_t, topic_thread_id,
                           current_pick, current_ban
                    FROM lobbies 
                    WHERE lobby_id = $1
                ''', lobby_id)
        except Exception as e:
            logger.error(f"Error getting lobby by ID {lobby_id}: {e}")
            return None

    async def get_lobby_by_match_id(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT lobby_id FROM lobbies WHERE match_id = $1', match_id)
                return result
        except Exception as e:
            logger.error(f"Error getting lobby by match_id {match_id}: {e}")
            return None

    async def get_lobby_info_cached(self, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                if isinstance(lobby_id, int) or (isinstance(lobby_id, str) and lobby_id.isdigit()):
                    return await conn.fetchrow('''
                        SELECT lobby_id, lobby_unique_id, status, max_players, current_players, 
                               match_confirmed, match_id, map_name, ct_captain_id, t_captain_id,
                               banned_maps, picked_map, match_score_ct, match_score_t, topic_thread_id,
                               current_pick, current_ban
                        FROM lobbies 
                        WHERE lobby_id = $1
                    ''', int(lobby_id))
                else:
                    return await conn.fetchrow('''
                        SELECT lobby_id, lobby_unique_id, status, max_players, current_players, 
                               match_confirmed, match_id, map_name, ct_captain_id, t_captain_id,
                               banned_maps, picked_map, match_score_ct, match_score_t, topic_thread_id,
                               current_pick, current_ban
                        FROM lobbies 
                        WHERE match_id = $1
                    ''', lobby_id)
        except Exception as e:
            logger.error(f"Error getting cached lobby info for {lobby_id}: {e}")
            return None

    async def get_lobby_players(self, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetch('''
                    SELECT p.user_id, p.nickname, lp.match_confirmed, lp.team, lp.pick_order, ps.rating
                    FROM lobby_players lp
                    JOIN players p ON lp.user_id = p.user_id
                    LEFT JOIN player_stats ps ON p.user_id = ps.user_id
                    WHERE lp.lobby_id = $1
                    ORDER BY lp.team, lp.pick_order
                ''', lobby_id)
        except Exception as e:
            logger.error(f"Error getting lobby players for lobby {lobby_id}: {e}")
            return []

    async def join_lobby(self, user_id, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Проверяем, не находится ли пользователь уже в каком-либо лобби
                    existing_lobby = await conn.fetchval(
                        'SELECT lobby_id FROM lobby_players WHERE user_id = $1', 
                        user_id
                    )
                    if existing_lobby:
                        return False, "Вы уже находитесь в лобби"
                    
                    # Получаем данные лобби с блокировкой
                    lobby_data = await conn.fetchrow(
                        'SELECT current_players, max_players, status FROM lobbies WHERE lobby_id = $1 FOR UPDATE', 
                        lobby_id
                    )
                    if not lobby_data:
                        return False, "Лобби не найдено"
                    
                    current_players, max_players, status = lobby_data['current_players'], lobby_data['max_players'], lobby_data['status']
                    
                    if status != 'waiting':
                        return False, "Лобби уже запущено"
                    
                    # Проверяем ДО добавления
                    if current_players >= max_players:
                        return False, "Лобби заполнено"
                    
                    # Добавляем игрока
                    joined_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await conn.execute(
                        'INSERT INTO lobby_players (lobby_id, user_id, joined_at) VALUES ($1, $2, $3)',
                        lobby_id, user_id, joined_at
                    )
                    
                    # Обновляем счетчик
                    new_count = current_players + 1
                    await conn.execute(
                        'UPDATE lobbies SET current_players = $1 WHERE lobby_id = $2',
                        new_count, lobby_id
                    )
                    
                    return True, "Успешно присоединились к лобби"
                    
        except Exception as e:
            logger.error(f"Error joining lobby {lobby_id} for user {user_id}: {e}")
            return False, f"Ошибка при присоединении: {str(e)}"

    async def process_filled_lobbies(self):
        """Проверяет и обрабатывает заполненные лобби"""
        try:
            async with self.pool.acquire() as conn:
                # Находим лобби, которые заполнены но еще в статусе waiting
                filled_lobbies = await conn.fetch('''
                    SELECT lobby_id, max_players, current_players 
                    FROM lobbies 
                    WHERE status = 'waiting' AND current_players >= max_players
                ''')
                
                for lobby in filled_lobbies:
                    lobby_id = lobby['lobby_id']
                    logger.info(f"Processing filled lobby {lobby_id}")
                    success = await self.auto_distribute_players(lobby_id)
                    if success:
                        logger.info(f"Successfully distributed players in lobby {lobby_id}")
                    else:
                        logger.error(f"Failed to distribute players in lobby {lobby_id}")
                        
        except Exception as e:
            logger.error(f"Error processing filled lobbies: {e}")

    async def auto_distribute_players(self, lobby_id):
        """Автоматическое распределение игроков по командам"""
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Блокируем запись лобби для предотвращения гонок
                    lobby_data = await conn.fetchrow(
                        'SELECT max_players, status, current_players FROM lobbies WHERE lobby_id = $1 FOR UPDATE', 
                        lobby_id
                    )
                    if not lobby_data:
                        logger.error(f"Lobby {lobby_id} not found for auto-distribution")
                        return False
                    
                    max_players = lobby_data['max_players']
                    status = lobby_data['status']
                    current_players = lobby_data['current_players']
                    
                    # Проверяем статус и заполненность
                    if status != 'waiting':
                        logger.info(f"Lobby {lobby_id} is in {status} status, skipping auto-distribution")
                        return False
                    
                    # Должно быть точное равенство
                    if current_players != max_players:
                        logger.info(f"Lobby {lobby_id} not exactly full ({current_players}/{max_players})")
                        return False
                    
                    # Получаем игроков с рейтингами
                    players_data = await conn.fetch('''
                        SELECT lp.user_id, p.nickname, COALESCE(ps.rating, 0) as rating
                        FROM lobby_players lp
                        JOIN players p ON lp.user_id = p.user_id
                        LEFT JOIN player_stats ps ON p.user_id = ps.user_id
                        WHERE lp.lobby_id = $1
                    ''', lobby_id)
                    
                    players_count = len(players_data)
                    logger.info(f"Auto-distribution: lobby {lobby_id}, players: {players_count}/{max_players}")
                    
                    # Финальная проверка
                    if players_count != max_players:
                        logger.error(f"Player count mismatch: {players_count} vs {max_players}")
                        return False
                    
                    # Сортируем игроков по рейтингу
                    players_sorted = sorted(players_data, key=lambda x: x['rating'], reverse=True)
                    players_per_team = max_players // 2
                    
                    if len(players_sorted) < 2:
                        logger.error(f"Not enough players for captains in lobby {lobby_id}")
                        return False
                    
                    # Выбираем капитанов (два лучших игрока)
                    ct_captain = players_sorted[0]['user_id']
                    t_captain = players_sorted[1]['user_id']
                    
                    # Распределяем игроков
                    ct_players = [ct_captain]
                    t_players = [t_captain]
                    
                    # Распределяем оставшихся игроков по рейтингу (змейкой для баланса)
                    remaining_players = players_sorted[2:]
                    
                    for i, player in enumerate(remaining_players):
                        if i % 2 == 0:
                            ct_players.append(player['user_id'])
                        else:
                            t_players.append(player['user_id'])
                    
                    # Обновляем команды игроков
                    for i, user_id in enumerate(ct_players):
                        await conn.execute('''
                            UPDATE lobby_players 
                            SET team = 'CT', pick_order = $1 
                            WHERE user_id = $2 AND lobby_id = $3
                        ''', i + 1, user_id, lobby_id)
                    
                    for i, user_id in enumerate(t_players):
                        await conn.execute('''
                            UPDATE lobby_players 
                            SET team = 'T', pick_order = $1 
                            WHERE user_id = $2 AND lobby_id = $3
                        ''', i + 1, user_id, lobby_id)
                    
                    # Обновляем статус лобби на фазу бана карт
                    await conn.execute('''
                        UPDATE lobbies 
                        SET ct_captain_id = $1, t_captain_id = $2, status = 'banning', 
                            current_ban = 0, banned_maps = '{}'
                        WHERE lobby_id = $3
                    ''', ct_captain, t_captain, lobby_id)
                    
                    logger.info(f"Auto-distribution completed for lobby {lobby_id}: CT={len(ct_players)}, T={len(t_players)}")
                    return True
                    
        except Exception as e:
            logger.error(f"Error auto-distributing players in lobby {lobby_id}: {e}")
            await self.reset_lobby_to_waiting(lobby_id)
            return False

    async def reset_lobby_to_waiting(self, lobby_id):
        """Сброс лобби в состояние waiting при ошибках распределения"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    UPDATE lobbies 
                    SET status = 'waiting', ct_captain_id = NULL, t_captain_id = NULL,
                        current_ban = 0, banned_maps = '{}'
                    WHERE lobby_id = $1
                ''', lobby_id)
                
                await conn.execute('''
                    UPDATE lobby_players 
                    SET team = NULL, pick_order = NULL 
                    WHERE lobby_id = $1
                ''', lobby_id)
                
                logger.info(f"Lobby {lobby_id} reset to waiting state due to distribution error")
        except Exception as e:
            logger.error(f"Error resetting lobby {lobby_id} to waiting state: {e}")

    async def validate_lobby_state(self, lobby_id):
        """Проверка целостности состояния лобби"""
        try:
            async with self.pool.acquire() as conn:
                lobby = await conn.fetchrow('SELECT current_players FROM lobbies WHERE lobby_id = $1', lobby_id)
                players = await conn.fetch('SELECT user_id FROM lobby_players WHERE lobby_id = $1', lobby_id)
                
                if lobby and len(players) != lobby['current_players']:
                    logger.warning(f"Lobby {lobby_id} state mismatch: {lobby['current_players']} vs {len(players)}")
                    await self.correct_lobby_player_count(lobby_id, len(players))
                    return False
                return True
        except Exception as e:
            logger.error(f"Error validating lobby state for {lobby_id}: {e}")
            return False

    async def correct_lobby_player_count(self, lobby_id, actual_count):
        """Коррекция счетчика игроков в лобби"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE lobbies SET current_players = $1 WHERE lobby_id = $2',
                    actual_count, lobby_id
                )
                logger.info(f"Corrected player count for lobby {lobby_id}: {actual_count}")
        except Exception as e:
            logger.error(f"Error correcting player count for lobby {lobby_id}: {e}")

    async def leave_lobby(self, user_id, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute('DELETE FROM lobby_players WHERE user_id = $1 AND lobby_id = $2', user_id, lobby_id)
                    await conn.execute('UPDATE lobbies SET current_players = GREATEST(0, current_players - 1) WHERE lobby_id = $1', lobby_id)
                    
                    lobby_info = await conn.fetchrow('SELECT status, current_players, max_players FROM lobbies WHERE lobby_id = $1', lobby_id)
                    if lobby_info and lobby_info['status'] == 'banning' and lobby_info['current_players'] < lobby_info['max_players']:
                        await conn.execute('UPDATE lobbies SET status = \'waiting\', match_confirmed = FALSE WHERE lobby_id = $1', lobby_id)
            
            logger.info(f"User {user_id} left lobby {lobby_id}")
            return True
        except Exception as e:
            logger.error(f"Error leaving lobby {lobby_id} for user {user_id}: {e}")
            return False

    async def is_user_in_lobby(self, user_id, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT 1 FROM lobby_players WHERE user_id = $1 AND lobby_id = $2', user_id, lobby_id)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking if user {user_id} is in lobby {lobby_id}: {e}")
            return False

    async def get_user_lobby(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('''
                    SELECT l.lobby_id 
                    FROM lobbies l
                    JOIN lobby_players lp ON l.lobby_id = lp.lobby_id
                    WHERE lp.user_id = $1 AND l.status = 'waiting'
                ''', user_id)
                return result
        except Exception as e:
            logger.error(f"Error getting user lobby for user {user_id}: {e}")
            return None

    async def get_ban_order(self, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow('SELECT ct_captain_id, t_captain_id FROM lobbies WHERE lobby_id = $1', lobby_id)
                if not result:
                    return None
                
                ct_captain, t_captain = result['ct_captain_id'], result['t_captain_id']
                
                # Проверка на None
                if ct_captain is None or t_captain is None:
                    logger.error(f"Captains not set for lobby {lobby_id}: CT={ct_captain}, T={t_captain}")
                    return None
                
                ct_rating = await self.get_player_rating(ct_captain)
                t_rating = await self.get_player_rating(t_captain)
                
                total_bans = 6
                ban_order = []
                
                if ct_rating >= t_rating:
                    ban_order = [
                        (ct_captain, 'CT'),
                        (t_captain, 'T'),
                        (ct_captain, 'CT'), 
                        (t_captain, 'T'),
                        (ct_captain, 'CT'),
                        (t_captain, 'T'),
                    ]
                else:
                    ban_order = [
                        (t_captain, 'T'),
                        (ct_captain, 'CT'),
                        (t_captain, 'T'),
                        (ct_captain, 'CT'),
                        (t_captain, 'T'),
                        (ct_captain, 'CT'),
                    ]
                
                return ban_order
        except Exception as e:
            logger.error(f"Error getting ban order for lobby {lobby_id}: {e}")
            return None

    async def get_current_ban_info(self, lobby_id):
        try:
            ban_order = await self.get_ban_order(lobby_id)
            if not ban_order:
                return None
                
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow('SELECT current_ban FROM lobbies WHERE lobby_id = $1', lobby_id)
                if not result:
                    return None
                    
                current_ban = result['current_ban'] if result['current_ban'] is not None else 0
                
                # Проверка границ массива
                if current_ban < 0 or current_ban >= len(ban_order):
                    return None
                    
                current_captain_id, current_team = ban_order[current_ban]
                captain_info = await self.get_player_by_id(current_captain_id)
                
                return {
                    'captain_id': current_captain_id,
                    'captain_nickname': captain_info['nickname'] if captain_info else "Неизвестно",
                    'team': current_team,
                    'ban_number': current_ban + 1,
                    'total_bans': len(ban_order)
                }
        except Exception as e:
            logger.error(f"Error getting current ban info for lobby {lobby_id}: {e}")
            return None

    async def ban_map_optimized(self, lobby_id, captain_id, map_name):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    lobby_data = await conn.fetchrow('SELECT current_ban, banned_maps, status FROM lobbies WHERE lobby_id = $1', lobby_id)
                    if not lobby_data:
                        return False, None
                    
                    current_ban, banned_maps, status = lobby_data['current_ban'], lobby_data['banned_maps'], lobby_data['status']
                    
                    if status != 'banning':
                        return False, None
                    
                    ban_order = await self.get_ban_order(lobby_id)
                    # Улучшенная проверка
                    if not ban_order or current_ban is None or current_ban < 0 or current_ban >= len(ban_order):
                        return False, None
                    
                    current_captain_id, current_team = ban_order[current_ban]
                    if current_captain_id != captain_id:
                        return False, None
                    
                    banned_maps = banned_maps or []
                    if map_name in banned_maps:
                        return False, None
                    
                    # Проверяем валидность карты
                    all_maps = ['SandStone', 'Province', 'Rust', 'Zone 7', 'Hanami', 'Breeze', 'Dune']
                    if map_name not in all_maps:
                        return False, None
                    
                    banned_maps.append(map_name)
                    
                    result = await conn.execute('''
                        UPDATE lobbies 
                        SET banned_maps = $1, current_ban = current_ban + 1 
                        WHERE lobby_id = $2 AND current_ban = $3
                    ''', banned_maps, lobby_id, current_ban)
                    
                    if result == "UPDATE 0":
                        return False, None
                    
                    available_maps = self.get_available_maps_fast(banned_maps)
                    match_id = None
                    needs_ui_update = False
                    
                    # Если осталась только одна карта - завершаем матч
                    if len(available_maps) == 1:
                        picked_map = available_maps[0]
                        match_id = await self.finalize_match(lobby_id, picked_map)
                        if not match_id:
                            logger.error(f"Failed to finalize match for lobby {lobby_id}")
                            return False, None
                        needs_ui_update = True
            
            logger.info(f"Map {map_name} banned in lobby {lobby_id}")
            return True, {"match_id": match_id, "needs_ui_update": needs_ui_update}
        except Exception as e:
            logger.error(f"Error banning map in lobby {lobby_id}: {e}")
            return False, None

    async def get_available_maps(self, lobby_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow('SELECT banned_maps FROM lobbies WHERE lobby_id = $1', lobby_id)
                banned_maps = result['banned_maps'] if result else []
                
                all_maps = ['SandStone', 'Province', 'Rust', 'Zone 7', 'Hanami', 'Breeze', 'Dune']
                available_maps = [map_name for map_name in all_maps if map_name not in banned_maps]
                
                return available_maps
        except Exception as e:
            logger.error(f"Error getting available maps for lobby {lobby_id}: {e}")
            return []

    def get_available_maps_fast(self, banned_maps):
        all_maps = ['SandStone', 'Province', 'Rust', 'Zone 7', 'Hanami', 'Breeze', 'Dune']
        banned_maps = banned_maps or []
        return [map_name for map_name in all_maps if map_name not in banned_maps]

    async def finalize_match(self, lobby_id, picked_map):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Генерируем уникальный match_id
                    match_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    lobby_data = await conn.fetchrow('SELECT ct_captain_id, t_captain_id FROM lobbies WHERE lobby_id = $1', lobby_id)
                    if not lobby_data:
                        logger.error(f"Lobby {lobby_id} not found for finalization")
                        return None
                    
                    ct_captain_id, t_captain_id = lobby_data['ct_captain_id'], lobby_data['t_captain_id']
                    
                    await conn.execute('''
                        INSERT INTO matches (match_id, lobby_id, map_name, ct_captain_id, t_captain_id, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    ''', match_id, lobby_id, picked_map, ct_captain_id, t_captain_id, created_at)
                    
                    await conn.execute('''
                        UPDATE lobbies 
                        SET status = 'ready', picked_map = $1, match_id = $2 
                        WHERE lobby_id = $3
                    ''', picked_map, match_id, lobby_id)
                    
                    await conn.execute('''
                        INSERT INTO match_players (match_id, user_id, team)
                        SELECT $1, user_id, team
                        FROM lobby_players WHERE lobby_id = $2 AND team IS NOT NULL
                    ''', match_id, lobby_id)
                    
                    # Создаем новое лобби для замены заполненного
                    await self.create_new_lobby()
            
            logger.info(f"Match {match_id} created with map {picked_map}")
            return match_id
        except Exception as e:
            logger.error(f"Error finalizing match in lobby {lobby_id}: {e}")
            return None

    async def get_match_details_for_topic(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                match_info = await conn.fetchrow('''
                    SELECT 
                        m.match_id,
                        m.map_name,
                        m.ct_captain_id,
                        m.t_captain_id,
                        p_ct.nickname as ct_captain_nickname,
                        p_ct.game_id as ct_captain_game_id,
                        p_t.nickname as t_captain_nickname,
                        p_t.game_id as t_captain_game_id,
                        l.lobby_id
                    FROM matches m
                    LEFT JOIN players p_ct ON m.ct_captain_id = p_ct.user_id
                    LEFT JOIN players p_t ON m.t_captain_id = p_t.user_id
                    LEFT JOIN lobbies l ON m.match_id = l.match_id
                    WHERE m.match_id = $1
                ''', match_id)
                
                if not match_info:
                    return None
                    
                players = await conn.fetch('''
                    SELECT 
                        mp.team,
                        p.nickname,
                        p.user_id,
                        COALESCE(ps.rating, 0) as rating
                    FROM match_players mp
                    JOIN players p ON mp.user_id = p.user_id
                    LEFT JOIN player_stats ps ON p.user_id = ps.user_id
                    WHERE mp.match_id = $1
                    ORDER BY mp.team, 
                             CASE WHEN mp.user_id IN ($2, $3) THEN 0 ELSE 1 END,
                             p.nickname
                ''', match_id, match_info['ct_captain_id'], match_info['t_captain_id'])
                
                return {
                    'match_info': match_info,
                    'players': players
                }
        except Exception as e:
            logger.error(f"Error getting match details for topic for match {match_id}: {e}")
            return None

    async def get_match_topic_by_match_id(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT topic_thread_id FROM lobbies WHERE match_id = $1', match_id)
                return result
        except Exception as e:
            logger.error(f"Error getting topic thread by match_id {match_id}: {e}")
            return None

    async def set_match_score(self, match_id, score_ct, score_t):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute('''
                        UPDATE matches 
                        SET score_ct = $1, score_t = $2 
                        WHERE match_id = $3
                    ''', score_ct, score_t, match_id)
                    
                    await conn.execute('''
                        UPDATE lobbies 
                        SET match_score_ct = $1, match_score_t = $2 
                        WHERE match_id = $3
                    ''', score_ct, score_t, match_id)
            
            logger.info(f"Score set for match {match_id}: CT {score_ct} - T {score_t}")
            return True
        except Exception as e:
            logger.error(f"Error setting score for match {match_id}: {e}")
            return False

    async def update_player_stats(self, user_id, match_id, kills, assists, deaths, points, win):
        try:
            if not (0 <= kills <= 125) or not (0 <= assists <= 125) or not (0 <= deaths <= 125) or not (0 <= points <= 125):
                return False, 0
            
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    existing = await conn.fetchval('SELECT 1 FROM stats_history WHERE user_id = $1 AND match_id = $2', user_id, match_id)
                    if existing:
                        return False, 0
                    
                    current_stats = await conn.fetchrow('''
                        SELECT rating, matches_played, kills, deaths, assists, points, wins, losses
                        FROM player_stats WHERE user_id = $1
                    ''', user_id)
                    
                    if not current_stats:
                        return False, 0
                    
                    current_rating = current_stats['rating']
                    current_matches = current_stats['matches_played']
                    current_kills = current_stats['kills']
                    current_deaths = current_stats['deaths']
                    current_assists = current_stats['assists']
                    current_points = current_stats['points']
                    current_wins = current_stats['wins']
                    current_losses = current_stats['losses']
                    
                    base_elo = 25 if win else -20
                    kill_elo = kills * 2
                    death_elo = -deaths
                    assist_elo = assists
                    elo_change = base_elo + kill_elo + death_elo + assist_elo
                    
                    new_rating = max(0, current_rating + elo_change)
                    new_matches = max(0, current_matches - 1)
                    new_kills = max(0, current_kills - kills)
                    new_deaths = max(0, current_deaths - deaths)
                    new_assists = max(0, current_assists - assists)
                    new_points = max(0, current_points - points)
                    new_wins = max(0, current_wins - (1 if win else 0))
                    new_losses = max(0, current_losses - (0 if win else 1))
                    
                    await conn.execute('''
                        UPDATE player_stats 
                        SET rating = $1, matches_played = $2, kills = $3, deaths = $4, 
                            assists = $5, points = $6, wins = $7, losses = $8
                        WHERE user_id = $9
                    ''', new_rating, new_matches, new_kills, new_deaths, new_assists, new_points, new_wins, new_losses, user_id)
                    
                    await conn.execute('''
                        UPDATE match_players 
                        SET kills = $1, deaths = $2, assists = $3, points = $4, win = $5, elo_change = $6
                        WHERE user_id = $7 AND match_id = $8
                    ''', kills, deaths, assists, points, win, elo_change, user_id, match_id)
                    
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await conn.execute('''
                        INSERT INTO stats_history (user_id, match_id, kills_added, deaths_added, assists_added, points_added, elo_change, win_added, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ''', user_id, match_id, kills, deaths, assists, points, elo_change, win, created_at)
                    
                    await self.update_top_players()
            
            logger.info(f"Stats updated for user {user_id} in match {match_id}: +{elo_change} ELO")
            return True, elo_change
        except Exception as e:
            logger.error(f"Error updating stats for user {user_id} in match {match_id}: {e}")
            return False, 0

    async def update_top_players(self):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    top_players = await conn.fetch('''
                        SELECT p.user_id, p.nickname, ps.rating, ps.matches_played, ps.kills, ps.deaths, ps.assists, ps.wins, ps.losses
                        FROM players p
                        JOIN player_stats ps ON p.user_id = ps.user_id
                        WHERE ps.matches_played > 0
                        ORDER BY ps.rating DESC 
                        LIMIT 100
                    ''')
                    
                    await conn.execute('DELETE FROM top_players')
                    
                    for i, player_data in enumerate(top_players):
                        user_id = player_data['user_id']
                        nickname = player_data['nickname']
                        rating = player_data['rating']
                        matches_played = player_data['matches_played']
                        kills = player_data['kills']
                        deaths = player_data['deaths']
                        assists = player_data['assists']
                        wins = player_data['wins']
                        losses = player_data['losses']
                        
                        kd_ratio = kills / deaths if deaths > 0 else kills
                        position = i + 1
                        
                        await conn.execute('''
                            INSERT INTO top_players (user_id, nickname, rating, matches_played, kills, deaths, assists, wins, losses, kd_ratio, position, last_updated)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, CURRENT_TIMESTAMP)
                        ''', user_id, nickname, rating, matches_played, kills, deaths, assists, wins, losses, kd_ratio, position)
            
            logger.info(f"Top players table updated with {len(top_players)} players")
            
        except Exception as e:
            logger.error(f"Error updating top players: {e}")

    async def revert_stats(self, user_id, match_id):
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    history_data = await conn.fetchrow('''
                        SELECT history_id, kills_added, deaths_added, assists_added, points_added, elo_change, win_added
                        FROM stats_history 
                        WHERE user_id = $1 AND match_id = $2
                    ''', user_id, match_id)
                    
                    if not history_data:
                        return False
                    
                    history_id = history_data['history_id']
                    kills_added = history_data['kills_added']
                    deaths_added = history_data['deaths_added']
                    assists_added = history_data['assists_added']
                    points_added = history_data['points_added']
                    elo_change = history_data['elo_change']
                    win_added = history_data['win_added']
                    
                    current_stats = await conn.fetchrow('''
                        SELECT rating, matches_played, kills, deaths, assists, points, wins, losses
                        FROM player_stats WHERE user_id = $1
                    ''', user_id)
                    
                    if not current_stats:
                        return False
                    
                    current_rating = current_stats['rating']
                    current_matches = current_stats['matches_played']
                    current_kills = current_stats['kills']
                    current_deaths = current_stats['deaths']
                    current_assists = current_stats['assists']
                    current_points = current_stats['points']
                    current_wins = current_stats['wins']
                    current_losses = current_stats['losses']
                    
                    new_rating = max(0, current_rating - elo_change)
                    new_matches = max(0, current_matches - 1)
                    new_kills = max(0, current_kills - kills_added)
                    new_deaths = max(0, current_deaths - deaths_added)
                    new_assists = max(0, current_assists - assists_added)
                    new_points = max(0, current_points - points_added)
                    new_wins = max(0, current_wins - (1 if win_added else 0))
                    new_losses = max(0, current_losses - (0 if win_added else 1))
                    
                    await conn.execute('''
                        UPDATE player_stats 
                        SET rating = $1, matches_played = $2, kills = $3, deaths = $4, 
                            assists = $5, points = $6, wins = $7, losses = $8
                        WHERE user_id = $9
                    ''', new_rating, new_matches, new_kills, new_deaths, new_assists, new_points, new_wins, new_losses, user_id)
                    
                    await conn.execute('DELETE FROM stats_history WHERE history_id = $1', history_id)
                    
                    await conn.execute('''
                        UPDATE match_players 
                        SET kills = 0, deaths = 0, assists = 0, points = 0, win = FALSE, elo_change = 0
                        WHERE user_id = $1 AND match_id = $2
                    ''', user_id, match_id)
                    
                    await self.update_top_players()
            
            logger.info(f"Stats reverted for user {user_id} in match {match_id}")
            return True
        except Exception as e:
            logger.error(f"Error reverting stats for user {user_id} in match {match_id}: {e}")
            return False

    async def cancel_match(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                players = await conn.fetch('SELECT user_id FROM match_players WHERE match_id = $1', match_id)
                
                for player in players:
                    user_id = player['user_id']
                    await self.revert_stats(user_id, match_id)
                
                await conn.execute('UPDATE matches SET status = \'cancelled\' WHERE match_id = $1', match_id)
            
            logger.info(f"Match {match_id} cancelled and stats reverted for all players")
            return True
        except Exception as e:
            logger.error(f"Error cancelling match {match_id}: {e}")
            return False

    async def uncancel_match(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('UPDATE matches SET status = \'active\' WHERE match_id = $1', match_id)
            
            logger.info(f"Match {match_id} uncancelled")
            return True
        except Exception as e:
            logger.error(f"Error uncancelling match {match_id}: {e}")
            return False

    async def add_screenshot_by_topic_thread(self, user_id, topic_thread_id, created_at=None):
        if not created_at:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            async with self.pool.acquire() as conn:
                match_id = await conn.fetchval('SELECT match_id FROM lobbies WHERE topic_thread_id = $1', topic_thread_id)
                if not match_id:
                    logger.error(f"No match found for topic thread {topic_thread_id}")
                    return None
                
                screenshot_id = await conn.fetchval('''
                    INSERT INTO screenshots (user_id, match_id, topic_thread_id, created_at) 
                    VALUES ($1, $2, $3, $4)
                    RETURNING screenshot_id
                ''', user_id, match_id, topic_thread_id, created_at)
                
                logger.info(f"Screenshot {screenshot_id} added for user {user_id} in topic {topic_thread_id}")
                return screenshot_id
        except Exception as e:
            logger.error(f"Error adding screenshot for user {user_id} in topic {topic_thread_id}: {e}")
            return None

    async def has_player_submitted_screenshot_by_topic(self, user_id, topic_thread_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('''
                    SELECT 1 FROM screenshots 
                    WHERE user_id = $1 AND topic_thread_id = $2
                ''', user_id, topic_thread_id)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking screenshot for user {user_id} in topic {topic_thread_id}: {e}")
            return False

    async def get_match_info_by_topic_thread(self, topic_thread_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchrow('''
                    SELECT 
                        m.match_id,
                        m.map_name,
                        m.ct_captain_id,
                        m.t_captain_id,
                        p_ct.nickname as ct_captain_nickname,
                        p_ct.game_id as ct_captain_game_id,
                        p_t.nickname as t_captain_nickname,
                        p_t.game_id as t_captain_game_id,
                        m.score_ct,
                        m.score_t,
                        m.status,
                        m.created_at
                    FROM matches m
                    JOIN lobbies l ON m.match_id = l.match_id
                    LEFT JOIN players p_ct ON m.ct_captain_id = p_ct.user_id
                    LEFT JOIN players p_t ON m.t_captain_id = p_t.user_id
                    WHERE l.topic_thread_id = $1
                ''', topic_thread_id)
        except Exception as e:
            logger.error(f"Error getting match info by topic thread {topic_thread_id}: {e}")
            return None

    async def get_match_players_by_topic_thread(self, topic_thread_id):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetch('''
                    SELECT 
                        mp.team,
                        p.nickname,
                        p.user_id,
                        COALESCE(ps.rating, 0) as rating
                    FROM match_players mp
                    JOIN matches m ON mp.match_id = m.match_id
                    JOIN lobbies l ON m.match_id = l.match_id
                    JOIN players p ON mp.user_id = p.user_id
                    LEFT JOIN player_stats ps ON p.user_id = ps.user_id
                    WHERE l.topic_thread_id = $1
                    ORDER BY mp.team, 
                             CASE WHEN mp.user_id IN (
                                 SELECT ct_captain_id FROM matches m2 
                                 JOIN lobbies l2 ON m2.match_id = l2.match_id 
                                 WHERE l2.topic_thread_id = $1
                             ) THEN 0 ELSE 1 END,
                             p.nickname
                ''', topic_thread_id)
        except Exception as e:
            logger.error(f"Error getting match players by topic thread {topic_thread_id}: {e}")
            return []

    async def get_all_time_top_players(self, limit=10):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetch('''
                    SELECT user_id, nickname, rating, matches_played, kills, deaths, assists, wins, losses, kd_ratio, position
                    FROM top_players 
                    ORDER BY position 
                    LIMIT $1
                ''', limit)
        except Exception as e:
            logger.error(f"Error getting all time top players: {e}")
            try:
                async with self.pool.acquire() as conn:
                    return await conn.fetch('''
                        SELECT p.user_id, p.nickname, ps.rating, ps.matches_played, ps.kills, ps.deaths, ps.assists, ps.wins, ps.losses
                        FROM players p
                        JOIN player_stats ps ON p.user_id = ps.user_id
                        WHERE ps.matches_played > 0
                        ORDER BY ps.rating DESC 
                        LIMIT $1
                    ''', limit)
            except Exception as e2:
                logger.error(f"Error getting fallback top players: {e2}")
                return []

    async def get_player_all_time_position(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT position FROM top_players WHERE user_id = $1', user_id)
                if result:
                    return result
                
                result = await conn.fetchval('''
                    SELECT COUNT(*) + 1
                    FROM players p1
                    JOIN player_stats ps1 ON p1.user_id = ps1.user_id
                    WHERE ps1.rating > (
                        SELECT ps2.rating 
                        FROM player_stats ps2 
                        WHERE ps2.user_id = $1
                    )
                    AND ps1.matches_played > 0
                ''', user_id)
                return result if result else 0
        except Exception as e:
            logger.error(f"Error getting all time position for user {user_id}: {e}")
            return 0

    async def get_player_has_any_stats(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT matches_played FROM player_stats WHERE user_id = $1', user_id)
                return result > 0 if result else False
        except Exception as e:
            logger.error(f"Error checking stats for user {user_id}: {e}")
            return False

    async def get_player_match_history(self, user_id, offset=0, limit=5):
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetch('''
                    SELECT 
                        m.match_id,
                        m.map_name,
                        m.score_ct,
                        m.score_t,
                        m.created_at,
                        mp.kills,
                        mp.assists,
                        mp.deaths,
                        mp.win,
                        mp.elo_change,
                        m.status
                    FROM matches m
                    JOIN match_players mp ON m.match_id = mp.match_id
                    WHERE mp.user_id = $1 
                    ORDER BY m.created_at DESC
                    LIMIT $2 OFFSET $3
                ''', user_id, limit, offset)
        except Exception as e:
            logger.error(f"Error getting match history for user {user_id}: {e}")
            return []

    async def get_player_match_history_count(self, user_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('''
                    SELECT COUNT(*)
                    FROM matches m
                    JOIN match_players mp ON m.match_id = mp.match_id
                    WHERE mp.user_id = $1
                ''', user_id)
                return result if result else 0
        except Exception as e:
            logger.error(f"Error getting match history count for user {user_id}: {e}")
            return 0

    async def get_match_topic_thread_id(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT topic_thread_id FROM lobbies WHERE match_id = $1', match_id)
                return result
        except Exception as e:
            logger.error(f"Error getting topic thread ID for match {match_id}: {e}")
            return None

    async def update_match_topic_thread_id(self, match_id, topic_thread_id):
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('UPDATE lobbies SET topic_thread_id = $1 WHERE match_id = $2', topic_thread_id, match_id)
            
            logger.info(f"Topic thread ID updated for match {match_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating topic thread ID for match {match_id}: {e}")
            return False

    async def get_match_id_by_topic_thread_id(self, topic_thread_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT match_id FROM lobbies WHERE topic_thread_id = $1', topic_thread_id)
                return result
        except Exception as e:
            logger.error(f"Error getting match ID by topic thread {topic_thread_id}: {e}")
            return None

    async def has_match_score_been_set(self, match_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchrow('SELECT score_ct, score_t FROM matches WHERE match_id = $1', match_id)
                if result:
                    score_ct, score_t = result['score_ct'], result['score_t']
                    return score_ct == 13 or score_t == 13
                return False
        except Exception as e:
            logger.error(f"Error checking match score for {match_id}: {e}")
            return False

    async def has_player_stats_been_registered(self, user_id, match_id):
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval('SELECT 1 FROM stats_history WHERE user_id = $1 AND match_id = $2', user_id, match_id)
                return result is not None
        except Exception as e:
            logger.error(f"Error checking player stats registration for {user_id} in {match_id}: {e}")
            return False

    async def close_connection(self):
        try:
            if self.pool:
                await self.pool.close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_connection()