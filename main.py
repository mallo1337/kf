# main.py
import asyncio
# ---- KEEP ALIVE SERVER FOR RENDER ----
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is alive')

def run_keep_alive_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("", port), PingHandler)
    print(f"[Render] Keep-alive HTTP server running on port {port}")
    server.serve_forever()

# запускаем сервер в отдельном потоке
threading.Thread(target=run_keep_alive_server, daemon=True).start()
# ---- END KEEP ALIVE ----
import os
import random
import string
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, MenuButtonCommands
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from database import Database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MODERATOR_GROUP_ID = os.getenv('MODERATOR_GROUP_ID')
CHANNEL_ID = os.getenv('CHANNEL_ID')
MENU_IMAGE_URL = os.getenv('MENU_IMAGE_URL')
PROFILE_IMAGE_URL = os.getenv('PROFILE_IMAGE_URL')
TOP_IMAGE_URL = os.getenv('TOP_IMAGE_URL')
SCREENSHOT_IMAGE_URL = os.getenv('SCREENSHOT_IMAGE_URL')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()

from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int = 1000):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def delete(self, key):
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        self.cache.clear()

user_lobby_messages = LRUCache(1000)
user_menu_messages = LRUCache(1000)

MAPS = ['🏜 SandStone', '🏘 Province', '🧱 Rust', '🏭 Zone 7', '🌸 Hanami', '🌊 Breeze', '🏝 Dune']
MAPS_RAW = ['SandStone', 'Province', 'Rust', 'Zone 7', 'Hanami', 'Breeze', 'Dune']

class RegistrationStates(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_game_id = State()

class ScreenshotStates(StatesGroup):
    waiting_for_screenshot = State()

class BanningStates(StatesGroup):
    waiting_for_ban = State()

def get_player_level(rating):
    levels = [
        (2001, "🔟"),
        (1751, "9️⃣"),
        (1531, "8️⃣"),
        (1351, "7️⃣"),
        (1201, "6️⃣"),
        (1051, "5️⃣"),
        (901, "4️⃣"),
        (751, "3️⃣"),
        (301, "2️⃣"),
        (0, "1️⃣")
    ]
    for threshold, level in levels:
        if rating >= threshold:
            return level
    return "1️⃣"

def get_registration_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Зарегистрироваться", callback_data="register")
    ]])

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top")],
        [InlineKeyboardButton(text="🌐 Поиск матча", callback_data="find_match")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")
    ]])

def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_registration")
    ]])

def get_screenshot_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_screenshot")
    ]])

def get_top_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_to_main")
    ]])

def get_profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🕗 История матчей", callback_data="match_history"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")
    ]])

def get_match_history_keyboard(user_id, current_offset=0, has_next=False, total_matches=0):
    keyboard = []
    
    limit = 5
    current_page = (current_offset // limit) + 1
    total_pages = (total_matches + limit - 1) // limit
    
    nav_buttons = []
    
    if current_offset > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"history_prev_{user_id}_{current_offset - limit}"))
    
    nav_buttons.append(InlineKeyboardButton(text="◀️ В меню", callback_data="back_to_main"))
    
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"history_next_{user_id}_{current_offset + limit}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_lobbies_keyboard(lobbies):
    keyboard = []
    for lobby in lobbies:
        lobby_id = lobby['lobby_id']
        lobby_unique_id = lobby['lobby_unique_id']
        player_count = lobby['current_players']
        max_players = lobby['max_players']
        
        game_mode = "2x2" if max_players == 4 else "5x5"
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"Лобби #{lobby_unique_id} ({game_mode}) {player_count}/{max_players}",
                callback_data=f"join_lobby_{lobby_id}"
            )
        ])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_lobby_keyboard(lobby_id, user_id, in_lobby=False):
    buttons = []
    
    if in_lobby:
        buttons.append([InlineKeyboardButton(text="🚪 Выйти из лобби", callback_data=f"leave_lobby_{lobby_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="✅ Присоединиться", callback_data=f"join_lobby_{lobby_id}")])
        buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_banning_keyboard(lobby_id, available_maps):
    buttons = []
    map_emojis = {
        'SandStone': '🏜',
        'Province': '🏘', 
        'Rust': '🧱',
        'Zone 7': '🏭',
        'Hanami': '🌸',
        'Breeze': '🌊',
        'Dune': '🏝'
    }
    
    for map_name in available_maps:
        emoji = map_emojis.get(map_name, '🗺')
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {map_name}",
                callback_data=f"ban_map_{lobby_id}_{map_name}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_ready_match_keyboard(topic_thread_id):
    if not topic_thread_id:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Обновить", callback_data="wait_for_topic")
        ]])
    
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📸 Отправить результаты матча", callback_data=f"send_screenshot_{topic_thread_id}")
    ]])

def is_admin(user_id):
    return user_id in ADMINS

def load_admins():
    try:
        with open('admins.txt', 'r', encoding='utf-8') as f:
            admins = []
            for line in f:
                line = line.strip()
                if line:
                    try:
                        admins.append(int(line))
                    except ValueError as e:
                        logger.error(f"Invalid admin ID format in admins.txt: {line} - {e}")
            logger.info(f"Loaded {len(admins)} admins")
            return admins
    except FileNotFoundError:
        logger.error("Admins file not found")
        return []
    except Exception as e:
        logger.error(f"Error loading admins: {e}")
        return []

ADMINS = load_admins()

async def safe_delete_message(chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramBadRequest as e:
        if "message to delete not found" in str(e):
            logger.debug(f"Message {message_id} already deleted")
            return True
        else:
            logger.warning(f"Error deleting message {message_id}: {e}")
            return False
    except Exception as e:
        logger.warning(f"Unexpected error deleting message {message_id}: {e}")
        return False

async def send_message_with_image(chat_id, text, image_url=None, reply_markup=None):
    try:
        if image_url:
            message = await bot.send_photo(chat_id=chat_id, photo=image_url, caption=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
        
        user_menu_messages.put(chat_id, message.message_id)
        return message
    except Exception as e:
        logger.error(f"Error sending message with image to {chat_id}: {e}")
        try:
            message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
            user_menu_messages.put(chat_id, message.message_id)
            return message
        except Exception as e2:
            logger.error(f"Error sending fallback message to {chat_id}: {e2}")
            return None

async def cleanup_user_messages(user_id):
    old_message_id = user_menu_messages.get(user_id)
    if old_message_id:
        try:
            await safe_delete_message(chat_id=user_id, message_id=old_message_id)
            user_menu_messages.delete(user_id)
        except Exception as e:
            logger.warning(f"Error cleaning up user message for {user_id}: {e}")

async def cleanup_lobby_messages(user_id):
    try:
        old_message_id = user_lobby_messages.get(user_id)
        user_lobby_messages.delete(user_id)
        if old_message_id:
            await safe_delete_message(chat_id=user_id, message_id=old_message_id)
    except Exception as e:
        logger.warning(f"Error in cleanup_lobby_messages for {user_id}: {e}")

async def set_menu_button(user_id):
    try:
        await bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonCommands(type="commands")
        )
    except Exception as e:
        logger.error(f"Error setting menu button: {e}")

async def set_bot_commands_for_user(user_id):
    commands = [
        types.BotCommand(command="start", description="🎮 Главное меню"),
        types.BotCommand(command="profile", description="👤 Мой профиль"),
    ]
    
    if is_admin(user_id):
        commands.extend([
            types.BotCommand(command="post", description="📢 Рассылка"),
            types.BotCommand(command="botstat", description="📊 Статистика бота"),
        ])
    
    try:
        await bot.set_my_commands(commands, scope=types.BotCommandScopeChat(chat_id=user_id))
    except Exception as e:
        logger.warning(f"Error setting commands for user {user_id}: {e}")
        try:
            await bot.set_my_commands(commands)
        except Exception as e2:
            logger.error(f"Error setting global commands: {e2}")

async def auto_leave_lobby_on_action(user_id):
    """Автоматический выход из лобби при любом действии пользователя (только если лобби не заполнено)"""
    try:
        lobby_id = await db.get_user_lobby(user_id)
        if lobby_id:
            lobby_info = await db.get_lobby_by_id(lobby_id)
            if lobby_info and lobby_info['status'] == 'waiting':
                await db.leave_lobby(user_id, lobby_id)
                await cleanup_lobby_messages(user_id)
                logger.info(f"User {user_id} automatically left lobby {lobby_id} on action")
                return True
        return False
    except Exception as e:
        logger.error(f"Error in auto_leave_lobby_on_action for user {user_id}: {e}")
        return False

async def update_player_lobby_message(user_id, lobby_text, reply_markup):
    """Обновление сообщения для одного игрока"""
    try:
        old_message_id = user_lobby_messages.get(user_id)
        if old_message_id:
            await safe_delete_message(chat_id=user_id, message_id=old_message_id)
        
        new_message = await bot.send_message(
            chat_id=user_id,
            text=lobby_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        user_lobby_messages.put(user_id, new_message.message_id)
        
    except Exception as e:
        logger.error(f"Error updating lobby message for user {user_id}: {e}")

async def update_player_lobby_message_safe(user_id, lobby_text, reply_markup):
    """Безопасное обновление сообщения для одного игрока с повторными попытками"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await update_player_lobby_message(user_id, lobby_text, reply_markup)
            return
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to update lobby message for user {user_id}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
            else:
                logger.error(f"Failed to update lobby message for user {user_id} after {max_retries} attempts")

async def update_lobby_interface(lobby_id):
    """Полное обновление интерфейса лобби для всех игроков"""
    try:
        # Получаем актуальные данные лобби
        lobby_info = await db.get_lobby_info_cached(lobby_id)
        if not lobby_info:
            logger.warning(f"Lobby {lobby_id} not found for interface update")
            return
        
        players = await db.get_lobby_players(lobby_id)
        lobby_text = await format_lobby_info(lobby_info, players)
        
        # Обновляем сообщения для всех игроков
        update_tasks = []
        for player in players:
            user_id = player['user_id']
            
            # Определяем клавиатуру в зависимости от статуса и роли
            if lobby_info['status'] == 'banning':
                # Только капитаны получают клавиатуру для бана
                if user_id in [lobby_info['ct_captain_id'], lobby_info['t_captain_id']]:
                    available_maps = await db.get_available_maps(lobby_id)
                    current_ban_info = await db.get_current_ban_info(lobby_id)
                    if current_ban_info and current_ban_info['captain_id'] == user_id:
                        reply_markup = get_banning_keyboard(lobby_id, available_maps)
                    else:
                        reply_markup = None
                else:
                    reply_markup = None
            elif lobby_info['status'] == 'ready':
                topic_thread_id = lobby_info['topic_thread_id']
                reply_markup = get_ready_match_keyboard(topic_thread_id)
            else:
                in_lobby = True
                reply_markup = get_lobby_keyboard(lobby_id, user_id, in_lobby)
            
            task = asyncio.create_task(
                update_player_lobby_message_safe(user_id, lobby_text, reply_markup)
            )
            update_tasks.append(task)
        
        # Ожидаем завершения всех обновлений
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)
            
        logger.info(f"Lobby interface updated for {len(players)} players in lobby {lobby_id}")
        
    except Exception as e:
        logger.error(f"Error updating lobby interface for {lobby_id}: {e}")

async def handle_lobby_distribution_complete(lobby_id):
    """Обработка завершения распределения игроков"""
    try:
        # Даем время на завершение транзакции в БД
        await asyncio.sleep(0.5)
        
        # Обновляем интерфейс для всех игроков
        await update_lobby_interface(lobby_id)
        
        # Запускаем таймер для авто-бана
        asyncio.create_task(handle_banning_timeout(lobby_id))
        
        logger.info(f"Lobby distribution handling completed for {lobby_id}")
        
    except Exception as e:
        logger.error(f"Error handling lobby distribution for {lobby_id}: {e}")

async def format_lobby_info(lobby_info, players):
    try:
        status = lobby_info['status']
        lobby_unique_id = lobby_info['lobby_unique_id']
        max_players = lobby_info['max_players']
        current_players = lobby_info['current_players']
        
        # Проверяем целостность данных
        if len(players) != current_players:
            logger.warning(f"Data mismatch in lobby {lobby_unique_id}: players={len(players)}, current_players={current_players}")
            # Исправляем расхождение
            await db.correct_lobby_player_count(lobby_info['lobby_id'], len(players))
            current_players = len(players)
        
        game_mode = "2x2" if max_players == 4 else "5x5"
        
        if status == 'waiting':
            text = f"✅ Лобби #{lobby_unique_id} ({game_mode})\n\n"
            text += f"👥 Игроков: {current_players}/{max_players}\n"
            
            if current_players < max_players:
                text += f"⏳ Ожидаем еще {max_players - current_players} игроков...\n\n"
            else:
                text += "🎯 Лобби заполнено! Распределение игроков...\n\n"
            
            text += "<b>👥 Список игроков:</b>\n"
            
            for i, player in enumerate(players, 1):
                level = get_player_level(player['rating'])
                text += f"{i}. {level} {player['nickname']} — {player['rating']} ELO\n"
            
        elif status == 'banning':
            text = f"🎲 Фаза бана карт ({game_mode})\n\n"
            
            current_ban_info = await db.get_current_ban_info(lobby_info['lobby_id'])
            if current_ban_info:
                text += f"⛔️ Сейчас банит: {current_ban_info['captain_nickname']}\n"
                text += f"📊 Бан {current_ban_info['ban_number']} из {current_ban_info['total_bans']}\n\n"
            else:
                text += "⏳ Загрузка информации о бане...\n\n"
            
            # Разделяем игроков по командам
            ct_players = [p for p in players if p['team'] == 'CT']
            t_players = [p for p in players if p['team'] == 'T']
            
            text += "🔵 CT\n"
            for i, player in enumerate(ct_players, 1):
                level = get_player_level(player['rating'])
                is_captain = " (Капитан)" if player['user_id'] == lobby_info['ct_captain_id'] else ""
                text += f"{level} {player['nickname']}{is_captain}\n"
                
            text += "\n🟠 T\n"
            for i, player in enumerate(t_players, 1):
                level = get_player_level(player['rating'])
                is_captain = " (Капитан)" if player['user_id'] == lobby_info['t_captain_id'] else ""
                text += f"{level} {player['nickname']}{is_captain}\n"
            
            # Показываем забаненные карты
            banned_maps = lobby_info['banned_maps'] or []
            if banned_maps:
                text += f"\n⛔️ Забаненные карты: {', '.join(banned_maps)}\n"
            
            # Показываем доступные карты
            available_maps = await db.get_available_maps(lobby_info['lobby_id'])
            if available_maps:
                text += f"\n🗺 Доступные карты: {', '.join(available_maps)}\n"
            
            if current_ban_info:
                captain_info = await db.get_player_by_id(current_ban_info['captain_id'])
                if captain_info:
                    text += f"\n⏳ Ожидаем, когда {captain_info['nickname']} выберет карту для бана..."
        
        elif status == 'ready':
            text = f'🎮 Матч #{lobby_info["match_id"]} ({game_mode})\n'
            text += f'🗺 Карта: {lobby_info["picked_map"]}\n'
            
            host_info = await db.get_player_by_id(lobby_info['ct_captain_id'])
            host_nickname = host_info['nickname'] if host_info else "Неизвестно"
            host_game_id = host_info['game_id'] if host_info else "Неизвестно"
            
            text += f'👤 Хостер: {host_game_id} ({host_nickname})\n\n'
            
            ct_players = [p for p in players if p['team'] == 'CT']
            text += "🔵 CT\n"
            for i, player in enumerate(ct_players, 1):
                level = get_player_level(player['rating'])
                text += f"{level} {player['nickname']}\n"
            
            text += "\n🟠 T\n"
            t_players = [p for p in players if p['team'] == 'T']
            for i, player in enumerate(t_players, 1):
                level = get_player_level(player['rating'])
                text += f"{level} {player['nickname']}\n"
            
            text += "\n❗️Все игроки должны зайти в игру в течении 5 минут"
            
        else:
            text = "❌ Неизвестный статус лобби"
        
        return text
        
    except Exception as e:
        logger.error(f"Error formatting lobby info: {e}")
        return "❌ Ошибка при загрузке информации о лобби"

async def create_match_forum_topic(match_id):
    if not match_id:
        logger.error("Attempted to create forum topic with None match_id")
        return None
        
    try:
        await asyncio.sleep(1)
        
        match_data = None
        for attempt in range(3):
            match_data = await db.get_match_details_for_topic(match_id)
            if match_data:
                break
            logger.warning(f"Match data not found for {match_id}, attempt {attempt + 1}")
            await asyncio.sleep(1)
            
        if not match_data:
            logger.error(f"Match data still not found for {match_id} after 3 attempts")
            return None
            
        match_info = match_data['match_info']
        players = match_data['players']
        
        topic_name = f"🎮 Матч #{match_id}"
        result = await bot.create_forum_topic(chat_id=MODERATOR_GROUP_ID, name=topic_name)
        topic_thread_id = result.message_thread_id
        
        map_emojis = {
            'SandStone': '🏜',
            'Province': '🏘',
            'Rust': '🧱', 
            'Zone 7': '🏭',
            'Hanami': '🌸',
            'Breeze': '🌊',
            'Dune': '🏝'
        }
        
        map_emoji = map_emojis.get(match_info['map_name'], '🗺')
        
        topic_text = f"📥 Новый матч #{match_id}\n\n"
        topic_text += f"🗺 Карта: {map_emoji} {match_info['map_name']}\n"
        topic_text += f"👤 Хостер: {match_info['ct_captain_nickname']} | ID: {match_info['ct_captain_game_id']}\n\n"
        
        ct_players = [p for p in players if p['team'] == 'CT']
        t_players = [p for p in players if p['team'] == 'T']
        
        topic_text += "🔵 CT\n"
        for player in ct_players:
            is_captain = " (Капитан)" if player['user_id'] == match_info['ct_captain_id'] else ""
            level_emoji = get_player_level(player['rating'])
            topic_text += f"{level_emoji} {player['nickname']} — {player['rating']} ELO{is_captain}\n"
            topic_text += f"TG_ID: <code>{player['user_id']}</code>\n"
        
        topic_text += "\n🟠 T\n"
        for player in t_players:
            is_captain = " (Капитан)" if player['user_id'] == match_info['t_captain_id'] else ""
            level_emoji = get_player_level(player['rating'])
            topic_text += f"{level_emoji} {player['nickname']} — {player['rating']} ELO{is_captain}\n"
            topic_text += f"TG_ID: <code>{player['user_id']}</code>\n"
        
        topic_text += "\n⏳ Ожидаем скриншоты с результатами матча....."
        
        await bot.send_message(
            chat_id=MODERATOR_GROUP_ID,
            message_thread_id=topic_thread_id,
            text=topic_text,
            parse_mode='HTML'
        )
        
        success = await db.update_match_topic_thread_id(match_id, topic_thread_id)
        if success:
            logger.info(f"Match topic created for match {match_id} with thread ID {topic_thread_id}")
        else:
            logger.error(f"Failed to update topic thread ID for match {match_id}")
        
        lobby_id = match_info['lobby_id']
        if lobby_id:
            await asyncio.sleep(1)
            await update_lobby_interface(lobby_id)
        
        return topic_thread_id
        
    except Exception as e:
        logger.error(f"Error creating match topic for {match_id}: {e}")
        return None

async def handle_banning_timeout(lobby_id):
    """Обработка таймаута при бане карт с улучшенной проверкой состояния"""
    try:
        # Ждем 30 секунд перед авто-баном
        await asyncio.sleep(30)
        
        lobby_info = await db.get_lobby_by_id(lobby_id)
        if not lobby_info or lobby_info['status'] != 'banning':
            return
            
        current_ban_info = await db.get_current_ban_info(lobby_id)
        if not current_ban_info:
            return
            
        # Двойная проверка состояния с задержкой
        await asyncio.sleep(1)
        
        lobby_info_updated = await db.get_lobby_by_id(lobby_id)
        if not lobby_info_updated or lobby_info_updated['status'] != 'banning':
            return
            
        current_ban_info_updated = await db.get_current_ban_info(lobby_id)
        if not current_ban_info_updated or current_ban_info_updated['ban_number'] != current_ban_info['ban_number']:
            return
            
        available_maps = await db.get_available_maps(lobby_id)
        if available_maps:
            random_map = random.choice(available_maps)
            captain_id = current_ban_info_updated['captain_id']
            
            logger.info(f"Auto-banning map {random_map} in lobby {lobby_id} for captain {captain_id}")
            
            success, result = await db.ban_map_optimized(lobby_id, captain_id, random_map)
            if success:
                logger.info(f"Auto-banned map {random_map} in lobby {lobby_id}")
                
                if result and result.get("match_id"):
                    match_id = result["match_id"]
                    asyncio.create_task(create_match_forum_topic(match_id))
                
                # Обновляем интерфейс лобби
                await update_lobby_interface(lobby_id)
                
    except Exception as e:
        logger.error(f"Error in handle_banning_timeout for lobby {lobby_id}: {e}")

async def lobby_monitor():
    """Фоновая задача для мониторинга и обработки лобби"""
    while True:
        try:
            await asyncio.sleep(2)  # Проверяем каждые 2 секунды
            await db.process_filled_lobbies()
        except Exception as e:
            logger.error(f"Error in lobby monitor: {e}")
            await asyncio.sleep(5)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    
    await auto_leave_lobby_on_action(user_id)
    
    await set_menu_button(user_id)
    await set_bot_commands_for_user(user_id)
    
    await cleanup_user_messages(user_id)
    
    if await db.is_user_registered(user_id):
        menu_text = f"👋 Привет, {first_name}!\n\nВыберите действие:"
        await send_message_with_image(
            message.chat.id, 
            menu_text, 
            MENU_IMAGE_URL, 
            get_main_keyboard()
        )
    else:
        start_message = await message.answer(
            f"👋 Привет, {first_name}!\n\nНажмите чтобы зарегистрироваться:", 
            reply_markup=get_registration_keyboard()
        )
        user_menu_messages.put(user_id, start_message.message_id)

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    await auto_leave_lobby_on_action(message.from_user.id)
    await show_profile(message.from_user.id)

async def show_profile(user_id):
    if not await db.is_user_registered(user_id):
        await bot.send_message(user_id, "❌ Вы не зарегистрированы!")
        return
    
    player_data = await db.get_player_profile(user_id)
    if not player_data:
        await bot.send_message(user_id, "❌ Профиль не найден!")
        return
    
    user_id_db = player_data['user_id']
    username = player_data['username']
    nickname = player_data['nickname']
    game_id = player_data['game_id']
    registration_date = player_data['registration_date']
    rating = player_data['rating']
    matches_played = player_data['matches_played']
    kills = player_data['kills']
    deaths = player_data['deaths']
    assists = player_data['assists']
    points = player_data['points']
    wins = player_data['wins']
    losses = player_data['losses']
    
    kd_ratio = kills / deaths if deaths > 0 else kills
    win_rate = (wins / matches_played * 100) if matches_played > 0 else 0
    level = get_player_level(rating)
    
    profile_text = (
        f'👤 Ваш профиль | {nickname}\n\n'
        f'🆔 {game_id}\n'
        f'📈 Уровень: {level}\n'
        f'🏆 ELO: {rating}\n\n'
        f'📊 Статистика:\n'
        f'• Убийств: {kills}\n'
        f'• Смертей: {deaths}\n'
        f'• K/D: {kd_ratio:.2f}\n'
        f'• Матчей сыграно: {matches_played}\n'
        f'• Побед: {wins}\n'
        f'• Поражений: {losses}\n'
        f'• Процент побед: {win_rate:.1f}%'
    )
    
    await cleanup_user_messages(user_id)
    await send_message_with_image(user_id, profile_text, PROFILE_IMAGE_URL, get_profile_keyboard())

@dp.message(Command("upd"))
async def cmd_update_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    
    if str(message.chat.id) != MODERATOR_GROUP_ID:
        await message.answer("❌ Эта команда работает только в группе модераторов!")
        return
    
    args = message.text.split()
    
    if len(args) == 7:
        try:
            tg_id, kills, assists, deaths, points, result = int(args[1]), int(args[2]), int(args[3]), int(args[4]), int(args[5]), args[6]
            
            if not (0 <= kills <= 125) or not (0 <= assists <= 125) or not (0 <= deaths <= 125) or not (0 <= points <= 125):
                await message.answer("❌ K/A/D/P должны быть от 0 до 125")
                return
            
            if result not in ['win', 'loss']:
                await message.answer("❌ Результат должен быть 'win' или 'loss'")
                return
            
            win = result == 'win'
            
            match_id = await db.get_match_id_by_topic_thread_id(message.message_thread_id)
            if not match_id:
                await message.answer("❌ Эта команда работает только в теме матча!")
                return
            
            if not await db.has_match_score_been_set(match_id):
                await message.answer("❌ Сначала установите счет матча командой /score X Y!")
                return
            
            if await db.has_player_stats_been_registered(tg_id, match_id):
                await message.answer(f"❌ Статистика для игрока {tg_id} уже зарегистрирована! Сначала откатите её командой /backupd {tg_id}")
                return
            
            success, elo_change = await db.update_player_stats(tg_id, match_id, kills, assists, deaths, points, win)
            
            if success:
                await message.answer(f"✅ Статистика для игрока {tg_id} в матче #{match_id} обновлена! ELO: {elo_change:+}")
            else:
                await message.answer("❌ Ошибка при обновлении статистики.")
                
        except ValueError:
            await message.answer("❌ Ошибка в формате чисел.")
    else:
        await message.answer("Используйте:\n/upd tg_id K A D P win/loss\n\nОграничение: 0-125 K/A/D/P\n\nПеред использованием /upd обязательно установите счет командой /score X Y!")

@dp.message(Command("score"))
async def cmd_set_score(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    
    if str(message.chat.id) != MODERATOR_GROUP_ID:
        await message.answer("❌ Эта команда работает только в группе модераторов!")
        return
    
    args = message.text.split()
    
    if len(args) == 3:
        try:
            score_ct, score_t = int(args[1]), int(args[2])
            
            if not (score_ct == 13 or score_t == 13):
                await message.answer("❌ Ошибка: хотя бы одна из команд должна набрать 13 раундов!")
                return
            
            if not (0 <= score_ct <= 13) or not (0 <= score_t <= 13):
                await message.answer("❌ Счёт должен быть от 0 до 13")
                return
            
            match_id = await db.get_match_id_by_topic_thread_id(message.message_thread_id)
            if not match_id:
                await message.answer("❌ Эта команда работает только в теме матча!")
                return
            
            if await db.set_match_score(match_id, score_ct, score_t):
                await message.answer(f"✅ Счёт установлен: CT {score_ct} - T {score_t}\n\nТеперь можно регистрировать статистику командой /upd")
            else:
                await message.answer("❌ Ошибка при установке счёта.")
                
        except ValueError:
            await message.answer("❌ Ошибка в формате чисел.")
    else:
        await message.answer("Используйте:\n/score X Y\n\nГде X - счёт CT, Y - счёт T (0-13), хотя бы одна команда должна набрать 13 раундов\n\nПосле установки счета можно регистрировать статистику командой /upd")

@dp.message(Command("cancel"))
async def cmd_cancel_match(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    
    if str(message.chat.id) != MODERATOR_GROUP_ID:
        await message.answer("❌ Эта команда работает только в группе модераторов!")
        return
    
    args = message.text.split()
    
    if len(args) == 1:
        match_id = await db.get_match_id_by_topic_thread_id(message.message_thread_id)
        if not match_id:
            await message.answer("❌ Эта команда работает только в теме матча!")
            return
        
        if await db.cancel_match(match_id):
            await message.answer(f"✅ Матч #{match_id} отменен! Вся статистика сброшена.")
        else:
            await message.answer("❌ Ошибка при отмене матча.")
    
    elif len(args) == 2 and args[1] == "back":
        match_id = await db.get_match_id_by_topic_thread_id(message.message_thread_id)
        if not match_id:
            await message.answer("❌ Эта команда работает только в теме матча!")
            return
        
        if await db.uncancel_match(match_id):
            await message.answer(f"✅ Матч #{match_id} восстановлен! Статистика снова доступна для регистрации.")
        else:
            await message.answer("❌ Ошибка при восстановлении матча.")
    else:
        await message.answer("Используйте:\n/cancel - для отмены матча\n/cancel back - для восстановления матча")

@dp.message(Command("backupd"))
async def cmd_revert_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    
    if str(message.chat.id) != MODERATOR_GROUP_ID:
        await message.answer("❌ Эта команда работает только в группе модераторов!")
        return
    
    args = message.text.split()
    
    if len(args) == 2:
        try:
            tg_id = int(args[1])
            
            match_id = await db.get_match_id_by_topic_thread_id(message.message_thread_id)
            if not match_id:
                await message.answer("❌ Эта команда работает только в теме матча!")
                return
            
            if await db.revert_stats(tg_id, match_id):
                player_info = await db.get_player_by_id(tg_id)
                player_nickname = player_info['nickname'] if player_info else f"ID {tg_id}"
                
                await message.answer(
                    f"✅ Статистика для игрока {player_nickname} в матче #{match_id} откачена!\n\n"
                    f"Теперь можно зарегистрировать статистику заново командой /upd"
                )
            else:
                await message.answer("❌ Ошибка при откате статистики.")
                
        except ValueError:
            await message.answer("❌ Ошибка в формате ID игрока.")
        except Exception as e:
            logger.error(f"Error in backupd command: {e}")
            await message.answer(f"❌ Ошибка при выполнении команды: {str(e)}")
    else:
        await message.answer("Используйте: /backupd tg_id\n\nДля отката статистики конкретного игрока в текущем матче\n\nПосле отката можно зарегистрировать статистику заново командой /upd")

@dp.message(Command("post"))
async def cmd_post(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    await message.answer("📢 Рассылка будет реализована позже")

@dp.message(Command("botstat"))
async def cmd_botstat(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав для этой команды.")
        return
    
    try:
        total_users = await db.pool.fetchval("SELECT COUNT(*) FROM players")
        
        today = datetime.now().strftime("%Y-%m-%d")
        new_users_today = await db.pool.fetchval("SELECT COUNT(*) FROM players WHERE registration_date >= $1", today)
        
        total_matches = await db.pool.fetchval("SELECT COUNT(*) FROM matches WHERE status = 'completed'")
        
        stat_text = (
            f"👥 Пользователи:\n"
            f"• Всего пользователей: {total_users}\n"
            f"• Новых за сегодня: {new_users_today}\n\n"
            f"🎮 Матчи:\n"
            f"• Всего сыгранных матчей: {total_matches}"
        )
        
        await message.answer(stat_text)
        
    except Exception as e:
        logger.error(f"Error getting bot stats: {e}")
        await message.answer(f"❌ Ошибка при получении статистики: {str(e)}")

@dp.callback_query(lambda c: c.data == "find_match")
async def show_available_lobbies(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    await cleanup_user_messages(user_id)
    await cleanup_lobby_messages(user_id)
    
    lobbies = await db.get_available_lobbies()
    
    if not lobbies:
        await send_message_with_image(user_id, "🌐 Поиск матча\n\n❌ Нет доступных лобби", None, get_lobbies_keyboard([]))
    else:
        await send_message_with_image(user_id, "🌐 Поиск матча\n\nВыберите лобби:", None, get_lobbies_keyboard(lobbies))
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("join_lobby_"))
async def join_lobby(callback_query: types.CallbackQuery):
    try:
        lobby_id = int(callback_query.data.replace("join_lobby_", ""))
        user_id = callback_query.from_user.id
        
        # Проверяем, не находится ли пользователь уже в лобби
        current_lobby = await db.get_user_lobby(user_id)
        if current_lobby:
            await callback_query.answer("❌ Вы уже находитесь в лобби", show_alert=True)
            return
        
        success, message_text = await db.join_lobby(user_id, lobby_id)
        
        if success:
            await callback_query.answer("✅ Вы присоединились к лобби")
            
            await cleanup_user_messages(user_id)
            
            # НЕМЕДЛЕННО обновляем интерфейс лобби
            await update_lobby_interface(lobby_id)
            
        else:
            await callback_query.answer(f"❌ {message_text}", show_alert=True)
            
    except ValueError:
        await callback_query.answer("❌ Неверный ID лобби", show_alert=True)
    except Exception as e:
        logger.error(f"Error joining lobby: {e}")
        await callback_query.answer("❌ Ошибка при присоединении к лобби", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("leave_lobby_"))
async def leave_lobby(callback_query: types.CallbackQuery):
    try:
        lobby_id = int(callback_query.data.replace("leave_lobby_", ""))
        user_id = callback_query.from_user.id
        
        if await db.leave_lobby(user_id, lobby_id):
            await callback_query.answer("✅ Вы вышли из лобби")
            await update_lobby_interface(lobby_id)
            await cleanup_lobby_messages(user_id)
            await show_available_lobbies(callback_query)
        else:
            await callback_query.answer("❌ Ошибка при выходе из лобби", show_alert=True)
    except ValueError:
        await callback_query.answer("❌ Неверный ID лобби", show_alert=True)
    except Exception as e:
        logger.error(f"Error leaving lobby: {e}")
        await callback_query.answer("❌ Ошибка при выходе из лобби", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("ban_map_"))
async def ban_map(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")
        lobby_id = int(data_parts[2])
        map_name = data_parts[3]
        captain_id = callback_query.from_user.id
        
        success, result = await db.ban_map_optimized(lobby_id, captain_id, map_name)
        if success:
            await callback_query.answer("✅ Карта забанена")
            
            # Обновляем интерфейс для ВСЕХ игроков
            await update_lobby_interface(lobby_id)
            
            # Если матч создан, создаем тему форума
            if result and result.get("match_id"):
                match_id = result["match_id"]
                asyncio.create_task(create_match_forum_topic(match_id))
            
        else:
            await callback_query.answer("❌ Ошибка при бане карты", show_alert=True)
    except (ValueError, IndexError) as e:
        await callback_query.answer("❌ Ошибка при бане карты", show_alert=True)
    except Exception as e:
        logger.error(f"Error banning map: {e}")
        await callback_query.answer("❌ Ошибка при бане карты", show_alert=True)

@dp.callback_query(lambda c: c.data == "register")
async def start_registration(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    if await db.is_user_registered(user_id):
        await callback_query.answer("❌ Вы уже зарегистрированы!", show_alert=True)
        return
    
    await state.set_state(RegistrationStates.waiting_for_nickname)
    
    await cleanup_user_messages(user_id)
    
    await send_message_with_image(user_id, "📝 Введите ваш игровой никнейм:", None, get_cancel_keyboard())
    await callback_query.answer()

@dp.message(RegistrationStates.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    nickname = message.text.strip()
    
    if len(nickname) < 3 or len(nickname) > 16:
        await message.answer("❌ Никнейм должен быть от 3 до 16 символов")
        return
    
    if not re.match(r'^[a-zA-Z0-9_]+$', nickname):
        await message.answer("❌ Никнейм должен содержать только английские буквы, цифры и подчеркивания")
        return
    
    await state.update_data(nickname=nickname)
    await state.set_state(RegistrationStates.waiting_for_game_id)
    
    await cleanup_user_messages(user_id)
    await send_message_with_image(
        user_id, 
        "🆔 Введите ваш игровой ID (8-13 цифр):", 
        None, 
        get_cancel_keyboard()
    )

@dp.message(RegistrationStates.waiting_for_game_id)
async def process_game_id(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    game_id = message.text.strip()
    data = await state.get_data()
    nickname = data.get('nickname')
    
    if not re.match(r'^\d{8,13}$', game_id):
        await message.answer("❌ Игровой ID должен содержать только цифры (8-13 символов)")
        return
    
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    
    success, message_text = await db.register_player(user_id, username, nickname, game_id)
    
    if success:
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Error deleting game_id message: {e}")
        
        await cleanup_user_messages(user_id)
        await state.clear()
        
        menu_text = f"✅ Регистрация завершена!\n\n👋 Добро пожаловать, {nickname}!\n\nВыберите действие:"
        await send_message_with_image(message.chat.id, menu_text, MENU_IMAGE_URL, get_main_keyboard())
    else:
        await message.answer(f"❌ {message_text}")

@dp.callback_query(lambda c: c.data == "cancel_registration")
async def cancel_registration(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    first_name = callback_query.from_user.first_name
    
    await cleanup_user_messages(user_id)
    
    if await db.is_user_registered(user_id):
        menu_text = f"👋 Привет, {first_name}!\n\nВыберите действие:"
        await send_message_with_image(user_id, menu_text, MENU_IMAGE_URL, get_main_keyboard())
    else:
        await send_message_with_image(
            user_id,
            f"👋 Привет, {first_name}!\n\nНажмите чтобы зарегистрироваться:",
            None,
            get_registration_keyboard()
        )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "profile")
async def show_profile_callback(callback_query: types.CallbackQuery):
    await auto_leave_lobby_on_action(callback_query.from_user.id)
    await show_profile(callback_query.from_user.id)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "match_history")
async def show_match_history(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    matches = await db.get_player_match_history(user_id, offset=0, limit=5)
    total_matches = await db.get_player_match_history_count(user_id)
    
    await cleanup_user_messages(user_id)
    
    if not matches:
        history_text = "🕗 История матчей\n\n❌ Вы еще не играли в матчах"
        await send_message_with_image(user_id, history_text, None, get_back_keyboard())
        await callback_query.answer()
        return
    
    limit = 5
    current_page = 1
    total_pages = (total_matches + limit - 1) // limit
    
    history_text = f"🎮 Сыгранные матчи (страница {current_page}/{total_pages}):\n\n"
    
    for i, match in enumerate(matches, 1):
        match_id = match['match_id']
        map_name = match['map_name']
        score_ct = match['score_ct']
        score_t = match['score_t']
        created_at = match['created_at']
        kills = match['kills']
        assists = match['assists']
        deaths = match['deaths']
        win = match['win']
        elo_change = match['elo_change']
        status = match['status']
        
        try:
            match_date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
        except ValueError:
            match_date = created_at
        
        map_emojis = {
            'SandStone': '🏜',
            'Province': '🏘',
            'Rust': '🧱', 
            'Zone 7': '🏭',
            'Hanami': '🌸',
            'Breeze': '🌊',
            'Dune': '🏝'
        }
        map_emoji = map_emojis.get(map_name, '🗺')
        
        history_text += f"🔶 Матч <code>{match_id}</code>\n"
        history_text += f"🗺 Карта: {map_emoji} {map_name}\n"
        
        if status == 'completed':
            outcome = "Победа✅" if win else "Поражение❌"
            elo_display = f"+{elo_change}" if elo_change > 0 else f"{elo_change}"
            
            history_text += f"🧾 Счёт: {score_ct} — {score_t}\n"
            history_text += f"⚔️ Исход матча: {outcome}\n"
            history_text += f"🏆 ELO: {elo_display}\n"
            history_text += f"📊 Статистика: {kills}/{assists}/{deaths} (K/A/D)\n"
        elif status == 'cancelled':
            history_text += f"⚔️ Исход матча: ❌ Отменён\n"
        else:
            history_text += f"⚔️ Исход матча: 🔎 На модерации\n"
        
        history_text += f"🗓 Дата: {match_date}\n"
        
        if i < len(matches):
            history_text += "\n" + "➖" * 20 + "\n\n"
    
    has_next = total_matches > 5
    
    await send_message_with_image(
        user_id, 
        history_text, 
        None, 
        get_match_history_keyboard(user_id, current_offset=0, has_next=has_next, total_matches=total_matches)
    )
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("history_prev_"))
async def show_prev_match_history(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")
        user_id = int(data_parts[2])
        offset = int(data_parts[3])
        
        if callback_query.from_user.id != user_id:
            await callback_query.answer("❌ Вы можете смотреть только свою историю", show_alert=True)
            return
        
        matches = await db.get_player_match_history(user_id, offset=offset, limit=5)
        total_matches = await db.get_player_match_history_count(user_id)
        
        if not matches:
            await callback_query.answer("❌ Больше матчей нет", show_alert=True)
            return
        
        limit = 5
        current_page = (offset // limit) + 1
        total_pages = (total_matches + limit - 1) // limit
        
        history_text = f"🎮 Сыгранные матчи (страница {current_page}/{total_pages}):\n\n"
        
        for i, match in enumerate(matches, 1):
            match_id = match['match_id']
            map_name = match['map_name']
            score_ct = match['score_ct']
            score_t = match['score_t']
            created_at = match['created_at']
            kills = match['kills']
            assists = match['assists']
            deaths = match['deaths']
            win = match['win']
            elo_change = match['elo_change']
            status = match['status']
            
            try:
                match_date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
            except ValueError:
                match_date = created_at
            
            map_emojis = {
                'SandStone': '🏜',
                'Province': '🏘',
                'Rust': '🧱', 
                'Zone 7': '🏭',
                'Hanami': '🌸',
                'Breeze': '🌊',
                'Dune': '🏝'
            }
            map_emoji = map_emojis.get(map_name, '🗺')
            
            history_text += f"🔶 Матч <code>{match_id}</code>\n"
            history_text += f"🗺 Карта: {map_emoji} {map_name}\n"
            
            if status == 'completed':
                outcome = "Победа✅" if win else "Поражение❌"
                elo_display = f"+{elo_change}" if elo_change > 0 else f"{elo_change}"
                
                history_text += f"🧾 Счёт: {score_ct} — {score_t}\n"
                history_text += f"⚔️ Исход матча: {outcome}\n"
                history_text += f"🏆 ELO: {elo_display}\n"
                history_text += f"📊 Статистика: {kills}/{assists}/{deaths} (K/A/D)\n"
            elif status == 'cancelled':
                history_text += f"⚔️ Исход матча: ❌ Отменён\n"
            else:
                history_text += f"⚔️ Исход матча: 🔎 На модерации\n"
            
            history_text += f"🗓 Дата: {match_date}\n"
            
            if i < len(matches):
                history_text += "\n" + "➖" * 20 + "\n\n"
        
        has_next = total_matches > offset + 5
        
        await cleanup_user_messages(user_id)
        await send_message_with_image(
            user_id, 
            history_text, 
            None, 
            get_match_history_keyboard(user_id, current_offset=offset, has_next=has_next, total_matches=total_matches)
        )
    except (ValueError, IndexError) as e:
        logger.error(f"Error in history_prev: {e}")
        await callback_query.answer("❌ Ошибка при загрузке истории", show_alert=True)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("history_next_"))
async def show_next_match_history(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split("_")
        user_id = int(data_parts[2])
        offset = int(data_parts[3])
        
        if callback_query.from_user.id != user_id:
            await callback_query.answer("❌ Вы можете смотреть только свою историю", show_alert=True)
            return
        
        matches = await db.get_player_match_history(user_id, offset=offset, limit=5)
        total_matches = await db.get_player_match_history_count(user_id)
        
        if not matches:
            await callback_query.answer("❌ Больше матчей нет", show_alert=True)
            return
        
        limit = 5
        current_page = (offset // limit) + 1
        total_pages = (total_matches + limit - 1) // limit
        
        history_text = f"🎮 Сыгранные матчи (страница {current_page}/{total_pages}):\n\n"
        
        for i, match in enumerate(matches, 1):
            match_id = match['match_id']
            map_name = match['map_name']
            score_ct = match['score_ct']
            score_t = match['score_t']
            created_at = match['created_at']
            kills = match['kills']
            assists = match['assists']
            deaths = match['deaths']
            win = match['win']
            elo_change = match['elo_change']
            status = match['status']
            
            try:
                match_date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
            except ValueError:
                match_date = created_at
            
            map_emojis = {
                'SandStone': '🏜',
                'Province': '🏘',
                'Rust': '🧱', 
                'Zone 7': '🏭',
                'Hanami': '🌸',
                'Breeze': '🌊',
                'Dune': '🏝'
            }
            map_emoji = map_emojis.get(map_name, '🗺')
            
            history_text += f"🔶 Матч <code>{match_id}</code>\n"
            history_text += f"🗺 Карта: {map_emoji} {map_name}\n"
            
            if status == 'completed':
                outcome = "Победа✅" if win else "Поражение❌"
                elo_display = f"+{elo_change}" if elo_change > 0 else f"{elo_change}"
                
                history_text += f"🧾 Счёт: {score_ct} — {score_t}\n"
                history_text += f"⚔️ Исход матча: {outcome}\n"
                history_text += f"🏆 ELO: {elo_display}\n"
                history_text += f"📊 Статистика: {kills}/{assists}/{deaths} (K/A/D)\n"
            elif status == 'cancelled':
                history_text += f"⚔️ Исход матча: ❌ Отменён\n"
            else:
                history_text += f"⚔️ Исход матча: 🔎 На модерации\n"
            
            history_text += f"🗓 Дата: {match_date}\n"
            
            if i < len(matches):
                history_text += "\n" + "➖" * 20 + "\n\n"
        
        has_next = total_matches > offset + 5
        
        await cleanup_user_messages(user_id)
        await send_message_with_image(
            user_id, 
            history_text, 
            None, 
            get_match_history_keyboard(user_id, current_offset=offset, has_next=has_next, total_matches=total_matches)
        )
    except (ValueError, IndexError) as e:
        logger.error(f"Error in history_next: {e}")
        await callback_query.answer("❌ Ошибка при загрузке истории", show_alert=True)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "top")
async def show_top(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    top_players = await db.get_all_time_top_players(10)
    
    top_text = "🏆 Топ игроков:\n\n"
    
    if not top_players:
        top_text += "❌ Пока нет игроков с статистикой\n\n"
    else:
        medals = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8.", "9.", "10."]
        
        for i, player_data in enumerate(top_players):
            user_id_player = player_data['user_id']
            nickname = player_data['nickname']
            rating = player_data['rating']
            matches_played = player_data['matches_played']
            kills = player_data['kills']
            deaths = player_data['deaths']
            assists = player_data['assists']
            wins = player_data['wins']
            losses = player_data['losses']
            
            kd_ratio = kills / deaths if deaths > 0 else kills
            level = get_player_level(rating)
            
            medal = medals[i] if i < len(medals) else f"{i+1}."
            
            top_text += f"{medal} {nickname} | {level}\n🏆 {rating} | K/D: {kd_ratio:.2f} | 🎮 {matches_played}\n\n"
    
    has_any_stats = await db.get_player_has_any_stats(user_id)
    
    if has_any_stats:
        player_position = await db.get_player_all_time_position(user_id)
        if player_position > 0:
            top_text += f"🎯 Ваша позиция: {player_position}"
        else:
            top_text += "⚠️ Вас нет в этом топе"
    else:
        top_text += "⚠️ Вас нет в этом топе"
    
    await cleanup_user_messages(user_id)
    await send_message_with_image(user_id, top_text, TOP_IMAGE_URL, get_top_keyboard())
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith("send_screenshot_"))
async def start_match_screenshot_upload(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        topic_thread_id = int(callback_query.data.replace("send_screenshot_", ""))
        user_id = callback_query.from_user.id
        
        if not topic_thread_id:
            await callback_query.answer("❌ Ошибка: тема матча не найдена", show_alert=True)
            return
        
        if not await db.is_user_registered(user_id):
            await callback_query.answer("❌ Вы не зарегистрированы!", show_alert=True)
            return
        
        if await db.has_player_submitted_screenshot_by_topic(user_id, topic_thread_id):
            await callback_query.answer("❌ Вы уже отправили скриншот для этого матча!", show_alert=True)
            return
        
        await state.set_state(ScreenshotStates.waiting_for_screenshot)
        await state.update_data(topic_thread_id=topic_thread_id)
        
        await cleanup_user_messages(user_id)
        await send_message_with_image(
            chat_id=user_id,
            text="📸 Отправьте скриншот с результатами матча\n\n",
            image_url=SCREENSHOT_IMAGE_URL,
            reply_markup=get_screenshot_cancel_keyboard()
        )
        
        logger.info(f"Screenshot upload started for topic {topic_thread_id} by user {user_id}")
    except ValueError:
        await callback_query.answer("❌ Ошибка: неверный идентификатор темы", show_alert=True)
    except Exception as e:
        logger.error(f"Error starting screenshot upload: {e}")
        await callback_query.answer("❌ Ошибка при начале загрузки скриншота", show_alert=True)
    await callback_query.answer()

@dp.message(ScreenshotStates.waiting_for_screenshot)
async def process_screenshot(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    topic_thread_id = data.get('topic_thread_id')
    
    if not message.photo:
        await message.answer("❌ Пожалуйста, отправьте скриншот как фото.")
        return
    
    if not topic_thread_id:
        await message.answer("❌ Ошибка: тема матча не найдена.")
        await state.clear()
        return
    
    logger.info(f"Processing screenshot for topic {topic_thread_id} from user {user_id}")
    
    try:
        test_message = await bot.send_message(
            chat_id=MODERATOR_GROUP_ID,
            message_thread_id=topic_thread_id,
            text="📸 Проверка темы для скриншота..."
        )
        await bot.delete_message(chat_id=MODERATOR_GROUP_ID, message_id=test_message.message_id)
    except Exception as e:
        logger.error(f"Topic {topic_thread_id} not accessible: {e}")
        await message.answer("❌ Тема матча не найдена или недоступна. Обратитесь к администратору.")
        await state.clear()
        return
    
    match_info = await db.get_match_info_by_topic_thread(topic_thread_id)
    match_id = match_info['match_id'] if match_info else "Неизвестно"
    
    photo_file_id = message.photo[-1].file_id
    screenshot_id = await db.add_screenshot_by_topic_thread(user_id, topic_thread_id)
    
    if not screenshot_id:
        await message.answer("❌ Ошибка при сохранении скриншота в базу данных.")
        await state.clear()
        return
    
    player_data = await db.get_player_profile(user_id)
    player_nickname = player_data['nickname'] if player_data else "Неизвестно"
    
    username = f"@{message.from_user.username}" if message.from_user.username else ""
    user_info = f"{username} {user_id}" if username else f"{user_id}"
    
    caption = (
        f"📸 Новый скриншот от {user_info}\n\n"
        f"Матч: #{match_id}\n"
        f"Игрок: {player_nickname}\n\n"
        f"Требуется ручная обработка❗️\n"
        f"Команды: /upd tg_id K A D P win/loss"
    )
    
    try:
        await bot.send_photo(
            chat_id=MODERATOR_GROUP_ID,
            message_thread_id=topic_thread_id,
            photo=photo_file_id,
            caption=caption,
            parse_mode='HTML'
        )
            
        await cleanup_user_messages(user_id)
        await bot.send_message(
            chat_id=user_id,
            text="✅ Скриншот отправлен на модерацию!\n\n📊 После проверки статистика будет автоматически обновлена.\n\nПо вопросам к администрации"
        )
        
        logger.info(f"Screenshot successfully sent for topic {topic_thread_id} by user {user_id}")
        
    except Exception as e:
        logger.error(f"Error sending screenshot: {e}")
        await bot.send_message(
            chat_id=user_id,
            text="❌ Ошибка при отправке скриншота. Попробуйте позже."
        )
    
    await state.clear()

@dp.callback_query(lambda c: c.data == "cancel_screenshot")
async def cancel_screenshot(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    
    await cleanup_user_messages(user_id)
    
    await bot.send_message(
        chat_id=user_id,
        text="❌ Отправка скриншота отменена"
    )
    
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "wait_for_topic")
async def wait_for_topic(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    lobby_id = await db.get_user_lobby(user_id)
    if not lobby_id:
        await callback_query.answer("❌ Вы не находитесь в лобби", show_alert=True)
        return
    
    lobby_info = await db.get_lobby_by_id(lobby_id)
    if not lobby_info:
        await callback_query.answer("❌ Лобби не найдено", show_alert=True)
        return
    
    match_id = lobby_info['match_id']
    if not match_id:
        await callback_query.answer("⏳ Матч еще создается...", show_alert=True)
        return
    
    topic_thread_id = await db.get_match_topic_thread_id(match_id)
    
    if topic_thread_id:
        try:
            await callback_query.message.edit_reply_markup(
                reply_markup=get_ready_match_keyboard(topic_thread_id)
            )
            await callback_query.answer("✅ Готово! Теперь можно отправить скриншот")
        except Exception as e:
            logger.error(f"Error updating button: {e}")
            await callback_query.answer("✅ Готово! Теперь можно отправить скриншот")
    else:
        await callback_query.answer("⏳ Подождите, матч еще создается...", show_alert=True)

@dp.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    
    await auto_leave_lobby_on_action(user_id)
    
    await state.clear()
    first_name = callback_query.from_user.first_name
    
    await cleanup_user_messages(user_id)
    await cleanup_lobby_messages(user_id)
    
    menu_text = f"👋 Привет, {first_name}!\n\nВыберите действие:"
    await send_message_with_image(user_id, menu_text, MENU_IMAGE_URL, get_main_keyboard())
    await callback_query.answer()

async def main():
    # Initialize database connection
    await db.connect_with_retry()
    
    # Запускаем монитор лобби
    asyncio.create_task(lobby_monitor())
    
    commands = [
        types.BotCommand(command="start", description="🎮 Главное меню"),
        types.BotCommand(command="profile", description="👤 Мой профиль"),
    ]
    
    try:
        await bot.set_my_commands(commands)
        logger.info("Bot commands set successfully")
    except Exception as e:
        logger.error(f"Error setting bot commands: {e}")
    
    print("Фейсит бот запущен...")
    logger.info("Faceit Bot starting...")
    
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        await db.close_connection()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
