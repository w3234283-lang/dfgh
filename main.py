import asyncio
import logging
import random
import sqlite3
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ─────────────── НАСТРОЙКИ ───────────────
BOT_TOKEN = "8325669732:AAGFvmLJMbAOilhKWElk43LG-FksHJpCfNk"
CRYPTO_TOKEN = "545475:AALkj6ssx8n0hVc2LR2ouWSat2YpoLCFUow"
ADMIN_ID = 7921743592
CHANNEL_ID = "@your_channel" 

CRYPTO_API = "https://pay.crypt.bot/api"
MIN_BET = 0.10
MAX_BET = 10.0

WIN_CHANCES = {
    "dice":       0.25,
    "evenodd":    0.40,
    "basketball": 0.30,
    "football":   0.28,
    "darts":      0.22,
}

WIN_MULTIPLIERS = {
    "dice":       4.5,
    "evenodd":    1.8,
    "basketball": 2.8,
    "football":   3.0,
    "darts":      4.0,
}

logging.basicConfig(level=logging.INFO)
db = sqlite3.connect("casino.db", check_same_thread=False)

# ─────────────── БД ───────────────
def init_db():
    db.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            game TEXT,
            choice TEXT,
            amount REAL,
            invoice_id TEXT,
            status TEXT DEFAULT 'pending',
            won INTEGER DEFAULT 0,
            payout REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS treasury (
            id INTEGER PRIMARY KEY CHECK (id=1),
            balance REAL DEFAULT 0
        )
    """)
    db.execute("INSERT OR IGNORE INTO treasury (id, balance) VALUES (1, 0)")
    db.commit()

def add_bet(user_id, username, game, choice, amount, invoice_id):
    db.execute(
        "INSERT INTO bets (user_id, username, game, choice, amount, invoice_id) VALUES (?,?,?,?,?,?)",
        (user_id, username, game, choice, amount, invoice_id)
    )
    db.commit()

def get_bet_by_invoice(invoice_id):
    return db.execute("SELECT * FROM bets WHERE invoice_id=?", (invoice_id,)).fetchone()

def update_bet_status(invoice_id, status, won, payout):
    db.execute(
        "UPDATE bets SET status=?, won=?, payout=? WHERE invoice_id=?",
        (status, won, payout, invoice_id)
    )
    db.commit()

def get_treasury():
    return db.execute("SELECT balance FROM treasury WHERE id=1").fetchone()[0]

def update_treasury(delta):
    db.execute("UPDATE treasury SET balance=balance+? WHERE id=1", (delta,))
    db.commit()

def get_stats():
    total_bets = db.execute("SELECT COUNT(*) FROM bets WHERE status='paid'").fetchone()[0]
    total_wagered = db.execute("SELECT COALESCE(SUM(amount),0) FROM bets WHERE status='paid'").fetchone()[0]
    total_wins = db.execute("SELECT COUNT(*) FROM bets WHERE won=1").fetchone()[0]
    total_paid_out = db.execute("SELECT COALESCE(SUM(payout),0) FROM bets WHERE won=1").fetchone()[0]
    return total_bets, total_wagered, total_wins, total_paid_out

# ─────────────── CRYPTO BOT API ───────────────
async def create_invoice(amount: float, payload: str) -> dict:
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{CRYPTO_API}/createInvoice", headers={
            "Crypto-Pay-API-Token": CRYPTO_TOKEN
        }, json={
            "asset": "USDT",
            "amount": str(round(amount, 2)),
            "payload": payload,
            "description": f"Казино: {payload.split('_')[1] if 'deposit' not in payload else 'Пополнение казны'}",
            "allow_comments": False,
            "allow_anonymous": False,
            "expires_in": 600
        })
        data = await r.json()
        return data.get("result", {})

async def create_check(amount: float) -> dict:
    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{CRYPTO_API}/createCheck", headers={
            "Crypto-Pay-API-Token": CRYPTO_TOKEN
        }, json={
            "asset": "USDT",
            "amount": str(round(amount, 2))
        })
        data = await r.json()
        return data.get("result", {})

async def get_invoices() -> list:
    async with aiohttp.ClientSession() as s:
        r = await s.get(f"{CRYPTO_API}/getInvoices", headers={
            "Crypto-Pay-API-Token": CRYPTO_TOKEN
        }, params={"status": "paid"})
        data = await r.json()
        return data.get("result", {}).get("items", [])

# ─────────────── FSM ───────────────
class BetState(StatesGroup):
    choosing_game = State()
    choosing_option = State()
    entering_amount = State()

class AdminState(StatesGroup):
    deposit_amount = State()
    withdraw_amount = State()

# ─────────────── КЛАВИАТУРЫ ───────────────
def games_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Кубик (угадай число)", callback_data="game_dice")],
        [InlineKeyboardButton(text="⚡ Чёт / Нечет", callback_data="game_evenodd")],
        [InlineKeyboardButton(text="🏀 Баскетбол (попадание)", callback_data="game_basketball")],
        [InlineKeyboardButton(text="⚽ Футбол (гол)", callback_data="game_football")],
        [InlineKeyboardButton(text="🎯 Дартс (в яблочко)", callback_data="game_darts")],
    ])

def evenodd_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="2️⃣ Чётное", callback_data="choice_even"),
            InlineKeyboardButton(text="1️⃣ Нечётное", callback_data="choice_odd"),
        ]
    ])

def dice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1️⃣", callback_data="choice_1"),
            InlineKeyboardButton(text="2️⃣", callback_data="choice_2"),
            InlineKeyboardButton(text="3️⃣", callback_data="choice_3"),
        ],
        [
            InlineKeyboardButton(text="4️⃣", callback_data="choice_4"),
            InlineKeyboardButton(text="5️⃣", callback_data="choice_5"),
            InlineKeyboardButton(text="6️⃣", callback_data="choice_6"),
        ],
    ])

def sport_keyboard(game):
    yes_text = {"basketball": "🏀 Попадёт!", "football": "⚽ Гол!", "darts": "🎯 В яблочко!"}
    no_text  = {"basketball": "❌ Промах", "football": "❌ Мимо", "darts": "❌ Мимо"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=yes_text[game], callback_data="choice_yes"),
            InlineKeyboardButton(text=no_text[game],  callback_data="choice_no"),
        ]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💰 Пополнить казну (CryptoBot)", callback_data="admin_deposit")],
        [InlineKeyboardButton(text="💸 Вывести из казны", callback_data="admin_withdraw")],
        [InlineKeyboardButton(text="💼 Баланс казны", callback_data="admin_balance")],
    ])

GAME_EMOJI = {"dice": "🎲", "evenodd": "⚡", "basketball": "🏀", "football": "⚽", "darts": "🎯"}
GAME_NAMES = {"dice": "Кубик", "evenodd": "Чёт/Нечет", "basketball": "Баскетбол", "football": "Футбол", "darts": "Дартс"}

# ─────────────── ОПРЕДЕЛЕНИЕ ПОБЕДИТЕЛЯ ───────────────
def determine_win(game: str, choice: str, dice_value: int) -> bool:
    rnd = random.random()
    win_chance = WIN_CHANCES[game]
    if game == "dice":
        if str(dice_value) == choice: return rnd < win_chance
        return False
    elif game == "evenodd":
        is_even = dice_value % 2 == 0
        user_picked_even = choice == "even"
        if is_even == user_picked_even: return rnd < win_chance
        return False
    elif game == "basketball":
        did_score = dice_value in [4, 5]
        user_said_yes = choice == "yes"
        if did_score == user_said_yes: return rnd < win_chance
        return False
    elif game == "football":
        did_score = dice_value in [3, 4, 5]
        user_said_yes = choice == "yes"
        if did_score == user_said_yes: return rnd < win_chance
        return False
    elif game == "darts":
        bullseye = dice_value == 6
        user_said_yes = choice == "yes"
        if bullseye == user_said_yes: return rnd < win_chance
        return False
    return False

def format_choice(game, choice):
    labels = {"even": "Чётное", "odd": "Нечётное", "yes": "Да", "no": "Нет"}
    if game == "dice": return f"Число {choice}"
    return labels.get(choice, choice)

# ─────────────── ИНИЦИАЛИЗАЦИЯ ───────────────
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─────────────── ХЭНДЛЕРЫ ───────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🎰 <b>Добро пожаловать в WinBet!</b>\n\n"
        "Выбери игру, поставь от <b>0.10 до 10 USDT</b> и испытай удачу!\n\n"
        "Оплата через <b>CryptoBot</b>. При выигрыше получаешь чек! 🤑",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть!", callback_data="play")]
        ])
    )

@dp.callback_query(F.data == "play")
async def cb_play(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(BetState.choosing_game)
    await call.message.edit_text("🎮 <b>Выбери игру:</b>", parse_mode="HTML", reply_markup=games_keyboard())

@dp.callback_query(F.data.startswith("game_"))
async def cb_choose_game(call: types.CallbackQuery, state: FSMContext):
    game = call.data.replace("game_", "")
    await state.update_data(game=game)
    await state.set_state(BetState.choosing_option)
    texts = {
        "dice": "🎲 <b>Кубик</b>\nУгадай число:", "evenodd": "⚡ <b>Чёт/Нечет</b>",
        "basketball": "🏀 <b>Баскетбол</b>", "football": "⚽ <b>Футбол</b>", "darts": "🎯 <b>Дартс</b>"
    }
    keyboards = {
        "dice": dice_keyboard(), "evenodd": evenodd_keyboard(),
        "basketball": sport_keyboard("basketball"), "football": sport_keyboard("football"), "darts": sport_keyboard("darts")
    }
    await call.message.edit_text(texts[game], parse_mode="HTML", reply_markup=keyboards[game])

@dp.callback_query(F.data.startswith("choice_"))
async def cb_choose_option(call: types.CallbackQuery, state: FSMContext):
    choice = call.data.replace("choice_", "")
    await state.update_data(choice=choice)
    await state.set_state(BetState.entering_amount)
    await call.message.edit_text("💵 <b>Введи сумму ставки (0.10 - 10.00 USDT):</b>", parse_mode="HTML")

@dp.message(BetState.entering_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount < MIN_BET or amount > MAX_BET: raise ValueError
    except ValueError:
        await message.answer(f"❌ Введи сумму от {MIN_BET} до {MAX_BET}")
        return

    data = await state.get_data()
    game, choice = data["game"], data["choice"]
    await state.clear()

    payload = f"{message.from_user.id}_{game}_{choice}_{amount}"
    invoice = await create_invoice(amount, payload)
    if not invoice: return

    add_bet(message.from_user.id, message.from_user.username or message.from_user.full_name, game, choice, amount, str(invoice["invoice_id"]))
    
    await message.answer(
        f"🎰 <b>Счёт создан!</b>\n\n"
        f"🎮 Игра: {GAME_NAMES[game]}\n💵 Сумма: {amount} USDT",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {amount} USDT", url=invoice["pay_url"])]
        ])
    )

# ─────────────── ФОНОВАЯ ПРОВЕРКА ОПЛАТ ───────────────
async def check_payments():
    while True:
        await asyncio.sleep(10)
        try:
            paid_invoices = await get_invoices()
            for inv in paid_invoices:
                payload = inv.get("payload", "")
                invoice_id = str(inv.get("invoice_id", ""))
                
                # ЛОГИКА ПОПОЛНЕНИЯ КАЗНЫ (АДМИН)
                if payload.startswith("deposit_"):
                    # Проверяем, не обрабатывали ли мы этот счет ранее
                    if db.execute("SELECT 1 FROM bets WHERE invoice_id=?", (invoice_id,)).fetchone():
                        continue
                    
                    amount = float(inv.get("amount"))
                    update_treasury(amount)
                    # Фиксируем пополнение в БД как спец-запись
                    add_bet(ADMIN_ID, "SYSTEM", "deposit", "admin", amount, invoice_id)
                    update_bet_status(invoice_id, "paid", 0, 0)
                    
                    try:
                        await bot.send_message(ADMIN_ID, f"✅ Казна успешно пополнена на <b>{amount} USDT</b>!", parse_mode="HTML")
                    except: pass
                    continue

                # ЛОГИКА СТАВОК (ИГРОКИ)
                bet = get_bet_by_invoice(invoice_id)
                if not bet or bet[7] == "paid": continue

                bet_id, user_id, username, game, choice, amount, inv_id, status, won, payout, created_at = bet
                dice_type_map = {"dice": "🎲", "evenodd": "🎲", "basketball": "🏀", "football": "⚽", "darts": "🎯"}
                
                try:
                    dice_msg = await bot.send_dice(chat_id=user_id, emoji=dice_type_map[game])
                    dice_value = dice_msg.dice.value
                    await asyncio.sleep(4)
                except: dice_value = random.randint(1, 6)

                is_win = determine_win(game, choice, dice_value)
                win_payout = round(amount * WIN_MULTIPLIERS[game], 2) if is_win else 0.0
                update_bet_status(invoice_id, "paid", 1 if is_win else 0, win_payout)

                if is_win:
                    update_treasury(-win_payout)
                    await bot.send_message(user_id, f"🏆 <b>ПОБЕДА!</b>\nВыигрыш: {win_payout} USDT", parse_mode="HTML")
                    check = await create_check(win_payout)
                    if check.get("bot_check_url"):
                        await bot.send_message(user_id, "🎁 Забирай чек:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🎁 Получить", url=check["bot_check_url"])]
                        ]))
                else:
                    update_treasury(amount)
                    await bot.send_message(user_id, f"😔 Не повезло. Выпало: {dice_value}")

                channel_text = f"{'🏆' if is_win else '💸'} @{username} | {GAME_NAMES[game]} | {amount} USDT | {'+' + str(win_payout) if is_win else 'Проигрыш'}"
                try: await bot.send_message(CHANNEL_ID, channel_text)
                except: pass

        except Exception as e: logging.error(f"Error: {e}")

# ─────────────── АДМИН ───────────────

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    balance = get_treasury()
    await message.answer(f"🛠 <b>Админ-панель</b>\nКазна: <b>{round(balance, 2)} USDT</b>", parse_mode="HTML", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_deposit")
async def admin_deposit_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.deposit_amount)
    await call.message.edit_text("💰 Введите сумму для пополнения через CryptoBot (USDT):")

@dp.message(AdminState.deposit_amount)
async def admin_deposit_process(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = float(message.text.strip())
        invoice = await create_invoice(amount, f"deposit_{message.from_user.id}")
        await state.clear()
        await message.answer(
            f"💳 Счёт на пополнение казны ({amount} USDT) создан.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Оплатить через Crypto Bot", url=invoice["pay_url"])],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
            ])
        )
    except: await message.answer("❌ Ошибка. Введите число.")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    t_bets, t_wag, t_wins, t_payout = get_stats()
    await call.message.edit_text(f"📊 Стат:\nСтавок: {t_bets}\nОборот: {t_wag} USDT\nПрибыль: {round(t_wag-t_payout, 2)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙", callback_data="admin_back")]]))

@dp.callback_query(F.data == "admin_withdraw")
async def admin_withdraw_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.withdraw_amount)
    await call.message.edit_text(f"💸 Казна: {get_treasury()} USDT. Сколько вывести?")

@dp.message(AdminState.withdraw_amount)
async def admin_withdraw_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        amount = float(message.text.strip())
        if amount > get_treasury(): return
        check = await create_check(amount)
        update_treasury(-amount)
        await state.clear()
        await message.answer(f"✅ Вывод готов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Чек", url=check["bot_check_url"])]]))
    except: pass

@dp.callback_query(F.data == "admin_back")
async def admin_back(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    balance = get_treasury()
    await call.message.edit_text(f"🛠 <b>Админ-панель</b>\nКазна: <b>{round(balance, 2)} USDT</b>", parse_mode="HTML", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_balance")
async def admin_bal_cb(call: types.CallbackQuery):
    await call.answer(f"Баланс: {get_treasury()} USDT", show_alert=True)

async def main():
    init_db()
    asyncio.create_task(check_payments())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
