import sqlite3
import os
import re
import sys
import time
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from datetime import datetime, timedelta
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError

# ================= ⚙️ البيانات الأساسية ================= #

TOKEN = "8732121402:AAE8u9nBUNIUKZQZ2U4vei-cnjgEq0GKhlA"
ADMIN_ID = 7896221838

# بيانات API الخاص بالتليجرام
API_ID = 38197866
API_HASH = "f4321737f836ac934273b65691e7684a"

DB_FILE = "data.db"
ACCOUNT_DIR = "account"  # مجلد حفظ الجلسات
STAR_PRICE_USD = 0.01

PAYMENT_CHANNEL = "@TTGOP15"
UPDATES_CHANNEL = "@TTGOP89"
SUPPORT_USER = "@R_T_OQ"

bot = telebot.TeleBot(TOKEN)

# إنشاء مجلد الحسابات والجلسات تلقائياً إن لم يكن موجوداً
if not os.path.exists(ACCOUNT_DIR):
    os.makedirs(ACCOUNT_DIR, exist_ok=True)

# قاموس لتخزين عمليات إنشاء الجلسات المؤقتة للأدمن
admin_login_data = {}

# ================= 📁 دوال إدارة ملفات الجلسات (account) ================= #

def save_session_to_file(phone, session_str):
    """حفظ نص الجلسة بملف دائم داخل مجلد account"""
    safe_phone = re.sub(r'[^\d]', '', str(phone))
    file_path = os.path.join(ACCOUNT_DIR, f"{safe_phone}.session")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(session_str)
        return file_path
    except Exception as e:
        print(f"خطأ أثناء حفظ ملف الجلسة: {e}")
        return None

def load_session_from_file(phone):
    """قراءة نص الجلسة من مجلد account"""
    safe_phone = re.sub(r'[^\d]', '', str(phone))
    file_path = os.path.join(ACCOUNT_DIR, f"{safe_phone}.session")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return None
    return None

def delete_session_file(phone):
    """حذف ملف الجلسة من مجلد account عند تسجيل الخروج"""
    safe_phone = re.sub(r'[^\d]', '', str(phone))
    file_path = os.path.join(ACCOUNT_DIR, f"{safe_phone}.session")
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

def sync_sessions_to_files():
    """مزامنة واسترجاع جميع الجلسات من قاعدة البيانات إلى مجلد account عند التشغيل"""
    rows = db_query('SELECT phone, session_str FROM stock WHERE is_sold = 0 OR is_sold = 2', fetchall=True)
    if rows:
        for phone, session_str in rows:
            if phone and session_str:
                if not load_session_from_file(phone):
                    save_session_to_file(phone, session_str)

# ================= 🛠️ دوال التشفير ================= #

def mask_user_id(uid):
    s = str(uid)
    if len(s) > 3:
        return s[:-3] + "***"
    return "***"

def mask_phone(phone):
    s = str(phone)
    if len(s) <= 4:
        return "****"
    mid = len(s) // 2
    start = max(0, mid - 2)
    end = min(len(s), start + 4)
    return s[:start] + "****" + s[end:]

# ================= 1. إدارة قاعدة البيانات SQLite ================= #

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            stars_recharged REAL DEFAULT 0.0,
            purchases INTEGER DEFAULT 0,
            invites INTEGER DEFAULT 0,
            last_gift TEXT DEFAULT NULL,
            banned INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            admin_id INTEGER PRIMARY KEY
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT DEFAULT '',
            phone TEXT,
            session_str TEXT,
            price REAL,
            is_sold INTEGER DEFAULT 0,
            user_id INTEGER DEFAULT NULL,
            created_at TEXT,
            purchased_at TEXT DEFAULT NULL,
            code_received INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('INSERT OR IGNORE INTO admins (admin_id) VALUES (?)', (ADMIN_ID,))
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("maintenance", "off")')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("buy_status", "on")')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("invite_status", "on")')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("usdt_address", "لم يحدد بعد")')
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES ("usdt_network", "TRC20")')
    
    conn.commit()
    conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = None
    if fetchone:
        res = cursor.fetchone()
    elif fetchall:
        res = cursor.fetchall()
    if commit:
        conn.commit()
    conn.close()
    return res

def get_user_data(user_id):
    user = db_query('SELECT user_id, balance, stars_recharged, purchases, invites, last_gift, banned FROM users WHERE user_id = ?', (user_id,), fetchone=True)
    if not user:
        db_query('INSERT INTO users (user_id) VALUES (?)', (user_id,), commit=True)
        user = db_query('SELECT user_id, balance, stars_recharged, purchases, invites, last_gift, banned FROM users WHERE user_id = ?', (user_id,), fetchone=True)
    
    return {
        'user_id': user[0],
        'balance': float(user[1]),
        'stars_recharged': float(user[2]),
        'purchases': user[3],
        'invites': user[4],
        'last_gift': datetime.fromisoformat(user[5]) if user[5] else None,
        'banned': bool(user[6])
    }

def update_user_field(user_id, field, value):
    db_query(f'UPDATE users SET {field} = ? WHERE user_id = ?', (value, user_id), commit=True)

def is_admin(user_id):
    if user_id == ADMIN_ID:
        return True
    res = db_query('SELECT admin_id FROM admins WHERE admin_id = ?', (user_id,), fetchone=True)
    return res is not None

def get_setting(key):
    res = db_query('SELECT value FROM settings WHERE key = ?', (key,), fetchone=True)
    return res[0] if res else None

def set_setting(key, value):
    db_query('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value), commit=True)

def check_subscription(user_id):
    forced_channel = get_setting('forced_channel')
    if not forced_channel or forced_channel == "":
        return True
    try:
        member = bot.get_chat_member(forced_channel, user_id)
        return member.status in ['creator', 'administrator', 'member']
    except Exception:
        return True

def check_access(user_id, chat_id=None):
    user = get_user_data(user_id)
    if user['banned']:
        if chat_id:
            bot.send_message(chat_id, "❌ **أنت محظور من استخدام هذا البوت.**", parse_mode="Markdown")
        return False
    
    if get_setting('maintenance') == 'on' and not is_admin(user_id):
        if chat_id:
            bot.send_message(chat_id, "🛠️ **البوت تحت الصيانة حالياً، يرجى المحاولة لاحقاً.**", parse_mode="Markdown")
        return False
        
    return True

# ================= 2. دالة التنظيف والتفقد التلقائي (الـ 5 دقائق) ================= #

def auto_cleanup_loop():
    """خيط يعمل باستمرار للتحقق من مهلة الـ 5 دقائق للأرقام المعلقة"""
    while True:
        try:
            now = datetime.now()
            pending_items = db_query('SELECT id, item_name, phone, session_str, price, user_id, purchased_at, code_received FROM stock WHERE is_sold = 2', fetchall=True)
            if pending_items:
                for item in pending_items:
                    stock_id, item_name, phone, session_str, price, uid, purchased_at_str, code_received = item
                    if purchased_at_str:
                        purchased_at = datetime.fromisoformat(purchased_at_str)
                        if (now - purchased_at).total_seconds() >= 300:  # 5 دقائق
                            if code_received == 0:
                                u = get_user_data(uid)
                                update_user_field(uid, 'balance', u['balance'] + price)
                                db_query('UPDATE stock SET is_sold = 0, user_id = NULL, purchased_at = NULL, code_received = 0 WHERE id = ?', (stock_id,), commit=True)
                                try:
                                    bot.send_message(
                                        uid, 
                                        f"❌ **انتهت مهلة الـ 5 دقائق ولم يتم استلام كود التحقق للرقم `{phone}`.**\n\n"
                                        f"🔄 تم إلغاء العملية وإعادة المبلغ **(${price:.2f})** إلى رصيدك تلقائياً.",
                                        parse_mode="Markdown"
                                    )
                                except Exception:
                                    pass
                            else:
                                file_session = load_session_from_file(phone) or session_str
                                try:
                                    client = TelegramClient(StringSession(file_session), API_ID, API_HASH)
                                    client.connect()
                                    if client.is_user_authorized():
                                        client.log_out()
                                    client.disconnect()
                                except Exception:
                                    pass
                                
                                delete_session_file(phone)
                                db_query('UPDATE stock SET is_sold = 1 WHERE id = ?', (stock_id,), commit=True)
                                try:
                                    bot.send_message(
                                        uid,
                                        f"⏱️ **انتهت مهلة 5 دقائق على شراء الرقم `{phone}`.**\n\n"
                                        f"🔒 تم تسجيل الخروج التلقائي من الجلسة وإغلاق العملية بنجاح.",
                                        parse_mode="Markdown"
                                    )
                                except Exception:
                                    pass
        except Exception as e:
            print(f"خطأ في خيط التنظيف التلقائي: {e}")
            
        time.sleep(15)

threading.Thread(target=auto_cleanup_loop, daemon=True).start()

# ================= 3. دالة جلب الكود من الجلسة ================= #

def fetch_telegram_code(session_str, phone=None):
    if phone:
        file_session = load_session_from_file(phone)
        if file_session:
            session_str = file_session

    client = None
    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        client.connect()
        
        if not client.is_user_authorized():
            client.disconnect()
            return None, "❌ الجلسة معطلة أو تم تسجيل الخروج منها من الحساب الأصلي."

        messages = client.get_messages(777000, limit=5)
        code = None
        full_msg = ""

        for msg in messages:
            if msg.text:
                full_msg = msg.text
                match = re.search(r'\b\d{5,6}\b', msg.text)
                if match:
                    code = match.group(0)
                    break

        client.disconnect()

        if code:
            return code, full_msg
        else:
            return None, "⏳ لم يصل كود التحقق بعد، يرجى طلب الكود من داخل التطبيق أولاً ثم إعادة المحاولة."

    except Exception as e:
        if client and client.is_connected():
            client.disconnect()
        return None, f"❌ حدث خطأ أثناء الاتصال بالجلسة: {str(e)}"

# ================= 4. الواجهات الرئيسية ================= #

def send_user_main_menu(chat_id, user_id):
    user = get_user_data(user_id)

    forced_channel = get_setting('forced_channel')
    if forced_channel and not check_subscription(user_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📢 اشترك بالقناة", url=f"https://t.me/{forced_channel.replace('@', '')}"))
        markup.add(InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub"))
        bot.send_message(
            chat_id, 
            f"⚠️ **يجب عليك الاشتراك في القناة لاستخدام البوت:**\n{forced_channel}", 
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    text = f"""🎉 **أهلاً بك في بوت بيع وشراء الأرقام العالمية!** ✨

🆔 **معرف الحساب:** `{user_id}`
💰 **رصيدك الحالي:** `${user['balance']:.3f}`
🛒 **إجمالي المشتريات:** `{user['purchases']}`

🚀 **اختر من القائمة أدناه للبدء:**"""

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛒 شراء أرقام", callback_data="buy_numbers"))
    markup.row(
        InlineKeyboardButton("👤 حسابي", callback_data="account"),
        InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily_gift")
    )
    markup.add(InlineKeyboardButton("💳 شحن رصيد", callback_data="recharge_menu"))
    markup.row(
        InlineKeyboardButton("🔝 تحويل رصيد", callback_data="transfer"),
        InlineKeyboardButton("👥 دعوة صديق", callback_data="invite")
    )
    markup.row(
        InlineKeyboardButton("⚠️ الشروط والتعليمات", callback_data="terms"),
        InlineKeyboardButton("🌐 اللغة", callback_data="language")
    )
    markup.row(
        InlineKeyboardButton("🔍 قناة الدفع", url=f"https://t.me/{PAYMENT_CHANNEL.replace('@', '')}"),
        InlineKeyboardButton("🔥 التحديثات", url=f"https://t.me/{UPDATES_CHANNEL.replace('@', '')}")
    )
    markup.add(InlineKeyboardButton("📞 الدعم الفني", url=f"https://t.me/{SUPPORT_USER.replace('@', '')}"))

    if is_admin(user_id):
        markup.add(InlineKeyboardButton("⚙️ لوحة تحكم الأدمن", callback_data="open_admin_panel"))

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or "لا يوجد"
    first_name = message.from_user.first_name or "مستخدم"

    user_exists = db_query('SELECT user_id FROM users WHERE user_id = ?', (user_id,), fetchone=True)
    if not user_exists:
        db_query('INSERT INTO users (user_id) VALUES (?)', (user_id,), commit=True)

        # فحص كود الدعوة فقط إذا كان نظام الدعوات مفتوحاً
        if get_setting('invite_status') == 'on':
            args = message.text.split()
            if len(args) > 1:
                try:
                    inviter_id = int(args[1])
                    if inviter_id != user_id:
                        inviter = db_query('SELECT user_id FROM users WHERE user_id = ?', (inviter_id,), fetchone=True)
                        if inviter:
                            db_query('UPDATE users SET invites = invites + 1, balance = balance + 0.005 WHERE user_id = ?', (inviter_id,), commit=True)
                            try:
                                bot.send_message(
                                    inviter_id, 
                                    f"🎉 **انضم مستخدم جديد عبر رابط الدعوة الخاص بك!**\n💰 تمت إضافة **$0.005** إلى رصيدك.", 
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                pass
                except ValueError:
                    pass

        total_users = db_query('SELECT COUNT(*) FROM users', fetchone=True)[0]
        try:
            bot.send_message(
                ADMIN_ID,
                f"👤 **عضو جديد انضم للبوت!**\n\n"
                f"• الاسم: **{first_name}**\n"
                f"• المعرف: @{username}\n"
                f"• الآيدي: `{user_id}`\n"
                f"• إجمالي الأعضاء: **{total_users}**",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    if not check_access(user_id, message.chat.id):
        return

    send_user_main_menu(message.chat.id, user_id)

# ================= 5. معالجة الشراء والجلب والخيارات ================= #

@bot.callback_query_handler(func=lambda call: True)
def callback_listener(call):
    user_id = call.from_user.id
    
    if not check_access(user_id):
        bot.answer_callback_query(call.id, "⚠️ البوت تحت الصيانة أو حسابك محظور.", show_alert=True)
        return

    user = get_user_data(user_id)

    if call.data == "check_sub":
        if check_subscription(user_id):
            bot.answer_callback_query(call.id, "✅ تم التحقق، شكراً لااشتراكك!")
            send_user_main_menu(call.message.chat.id, user_id)
        else:
            bot.answer_callback_query(call.id, "❌ لم تشترك بالقناة بعد!", show_alert=True)
        return

    elif call.data == "back_to_main" or call.data == "open_user_panel":
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        send_user_main_menu(call.message.chat.id, user_id)
        return

    elif call.data == "buy_numbers":
        if get_setting('buy_status') == 'off' and not is_admin(user_id):
            bot.answer_callback_query(call.id, "⚠️ قسم شراء الأرقام متوقف حالياً للصيانة، يرجى المحاولة لاحقاً.", show_alert=True)
            return

        categories = db_query('SELECT item_name, price, COUNT(*) FROM stock WHERE is_sold = 0 GROUP BY item_name, price', fetchall=True)
        
        if not categories:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("رجوع", callback_data="back_to_main"))
            bot.edit_message_text(
                "🛒 **قسم شراء الأرقام**\n\n❌ لا توجد أرقام متوفرة حالياً في المخزون.", 
                call.message.chat.id, 
                call.message.message_id, 
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        
        markup = InlineKeyboardMarkup()
        for idx, cat in enumerate(categories):
            item_name, price, count = cat[0] or "سلعة بدون اسم", float(cat[1]), cat[2]
            markup.add(InlineKeyboardButton(f"🛍️ {item_name} (المتبقي: {count}) — ${price:.2f}", callback_data=f"buy_cat_{idx}"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="back_to_main"))
        
        bot.edit_message_text(
            "🛒 **اختر السلعة المطلوبة للشراء:**", 
            call.message.chat.id, 
            call.message.message_id, 
            reply_markup=markup, 
            parse_mode="Markdown"
        )

    elif call.data.startswith("buy_cat_"):
        if get_setting('buy_status') == 'off' and not is_admin(user_id):
            bot.answer_callback_query(call.id, "⚠️ قسم الشراء مقفل حالياً.", show_alert=True)
            return

        idx = int(call.data.replace("buy_cat_", ""))
        categories = db_query('SELECT item_name, price FROM stock WHERE is_sold = 0 GROUP BY item_name, price', fetchall=True)
        
        if idx >= len(categories):
            bot.answer_callback_query(call.id, "❌ حدث خطأ، أعد فتح القائمة.", show_alert=True)
            return
            
        target_name, price = categories[idx][0], float(categories[idx][1])
        item = db_query('SELECT id, phone, session_str FROM stock WHERE item_name = ? AND is_sold = 0 LIMIT 1', (target_name,), fetchone=True)
        
        if not item:
            bot.answer_callback_query(call.id, "❌ نفدت الكمية من هذه السلعة!", show_alert=True)
            return
        
        stock_id, phone, session_str = item[0], item[1], item[2]
        
        if user['balance'] < price:
            bot.answer_callback_query(call.id, f"❌ رصيدك غير كافٍ! السعر ${price:.2f} ورصيدك ${user['balance']:.3f}", show_alert=True)
            return
        
        now_str = datetime.now().isoformat()
        new_bal = user['balance'] - price
        update_user_field(user_id, 'balance', new_bal)
        update_user_field(user_id, 'purchases', user['purchases'] + 1)
        
        db_query('UPDATE stock SET is_sold = 2, user_id = ?, purchased_at = ?, code_received = 0 WHERE id = ?', (user_id, now_str, stock_id), commit=True)
        
        bot.answer_callback_query(call.id, f"✅ تم حجز الرقم بنجاح!", show_alert=True)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📩 جلب الكود", callback_data=f"get_code_{stock_id}"),
            InlineKeyboardButton("🚪 تسجيل خروج من الجلسة", callback_data=f"logout_code_{stock_id}")
        )
        
        bot.send_message(
            call.message.chat.id,
            f"🎉 **تمت عملية حجز الرقم بنجاح!**\n\n"
            f"📦 **السلعة:** `{target_name}`\n"
            f"📱 **الرقم:** `{phone}`\n"
            f"💵 **السعر:** `${price:.2f}`\n\n"
            f"⏱️ **ملاحظة هامة:** لديك **5 دقائق** لجلب الكود وإلا تُكتنس العملية وتُسترجع الأموال.\n"
            f"👇 **اطلب الكود من التطبيق ثم اضغط على الزر أدناه:**",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif call.data.startswith("get_code_"):
        stock_id = int(call.data.replace("get_code_", ""))
        item = db_query('SELECT item_name, phone, session_str, user_id, is_sold, price FROM stock WHERE id = ?', (stock_id,), fetchone=True)
        
        if not item or item[3] != user_id or item[4] == 1:
            bot.answer_callback_query(call.id, "❌ انتهت مهلة هذه الجلسة أو تمت العملية مسبقاً!", show_alert=True)
            return

        item_name, phone_num, session_str, price = item[0], item[1], item[2], item[5]
        
        bot.answer_callback_query(call.id, "⏳ جاري الاتصال بالجلسة وقراءة الكود...")
        
        code, msg_text = fetch_telegram_code(session_str, phone=phone_num)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🔄 إعادة جلب الكود", callback_data=f"get_code_{stock_id}"),
            InlineKeyboardButton("🚪 تسجيل خروج من الجلسة", callback_data=f"logout_code_{stock_id}")
        )
        
        if code:
            db_query('UPDATE stock SET code_received = 1 WHERE id = ?', (stock_id,), commit=True)
            
            bot.send_message(
                call.message.chat.id,
                f"✅ **تم جلب كود التحقق بنجاح!**\n\n"
                f"📱 **الرقم:** `{phone_num}`\n"
                f"🔑 **كود التحقق:** `{code}`\n\n"
                f"💬 **نص الرسالة:**\n`{msg_text}`\n\n"
                f"⚠️ **يرجى الضغط على زر تسجيل الخروج فور إتمام العملية.**",
                reply_markup=markup,
                parse_mode="Markdown"
            )

            # 📩 1. إرسال إشعار مالك البوت عند استلام الكود
            try:
                bot.send_message(
                    ADMIN_ID,
                    f"🔔 **تفعيل رقم جديد بنجاح!**\n\n"
                    f"👤 **المشتري:** `{user_id}`\n"
                    f"📦 **السلعة:** `{item_name}`\n"
                    f"📱 **الرقم:** `{phone_num}`\n"
                    f"🔑 **كود التحقق:** `{code}`\n"
                    f"⏰ **الوقت:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            # 📢 2. إرسال إثبات الشراء لقناة التفعيلات
            try:
                bot.send_message(
                    PAYMENT_CHANNEL,
                    f"✅ **عملية شراء وتفعيل جديدة!**\n\n"
                    f"📦 **السلعة:** `{item_name}`\n"
                    f"📱 **الرقم:** `{mask_phone(phone_num)}`\n"
                    f"💵 **السعر:** `${price:.2f}`\n"
                    f"👤 **المشتري:** `{mask_user_id(user_id)}`\n"
                    f"⏰ **التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    f"🤖 **تفعيل آلي وعبر البوت الرسمي فقط.**",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"خطأ إرسال لقناة التفعيلات: {e}")

        else:
            bot.send_message(
                call.message.chat.id, 
                f"📱 **الرقم:** `{phone_num}`\n\n{msg_text}", 
                reply_markup=markup,
                parse_mode="Markdown"
            )

    elif call.data.startswith("logout_code_"):
        stock_id = int(call.data.replace("logout_code_", ""))
        item = db_query('SELECT phone, session_str, user_id FROM stock WHERE id = ?', (stock_id,), fetchone=True)
        
        if not item or item[2] != user_id:
            bot.answer_callback_query(call.id, "❌ ليس لديك صلاحية لهذه الجلسة!", show_alert=True)
            return

        phone_num, session_str = item[0], item[1]
        file_session = load_session_from_file(phone_num) or session_str

        bot.answer_callback_query(call.id, "⏳ جاري تسجيل الخروج من الجلسة...")

        client = TelegramClient(StringSession(file_session), API_ID, API_HASH)
        try:
            client.connect()
            if client.is_user_authorized():
                client.log_out()
            client.disconnect()
        except Exception:
            pass

        delete_session_file(phone_num)
        db_query('UPDATE stock SET is_sold = 1 WHERE id = ?', (stock_id,), commit=True)
        
        bot.send_message(
            call.message.chat.id,
            f"✅ **تم تسجيل الخروج بنجاح واكتملت العملية للرقم `{phone_num}`!**",
            parse_mode="Markdown"
        )

    elif call.data == "account":
        acc_text = (
            f"👤 **تفاصيل حسابك**\n\n"
            f"🆔 **الآيدي:** `{user_id}`\n"
            f"💰 **الرصيد الحالي:** `${user['balance']:.3f}`\n"
            f"⭐ **النجوم المشحونة:** `{user['stars_recharged']:.2f}`\n"
            f"🛒 **عدد المشتريات:** `{user['purchases']}`\n"
            f"👥 **عدد الدعوات:** `{user['invites']}`"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("رجوع", callback_data="back_to_main"))
        bot.edit_message_text(acc_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "daily_gift":
        now = datetime.now()
        last_gift = user['last_gift']
        if last_gift is None or (now - last_gift) >= timedelta(days=1):
            new_bal = user['balance'] + 0.01
            update_user_field(user_id, 'balance', new_bal)
            update_user_field(user_id, 'last_gift', now.isoformat())
            bot.answer_callback_query(call.id, "🎁 تم استلام الهدية اليومية ($0.01) بنجاح!", show_alert=True)
        else:
            time_left = timedelta(days=1) - (now - last_gift)
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            bot.answer_callback_query(call.id, f"⏳ عد بعد {hours} ساعة و {minutes} دقيقة.", show_alert=True)

    elif call.data == "transfer":
        msg = bot.send_message(
            call.message.chat.id, 
            "🔝 **تحويل رصيد إلى مستخدم آخر**\n\nأرسل **آيدي المستلم** متبوعاً بـ **المبلغ** بمسافة بينهم.\nمثال: `12345678 1.5`", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_transfer)

    elif call.data == "recharge_menu":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⭐ شحن عبر نجوم تيليجرام", callback_data="recharge_stars"))
        markup.add(InlineKeyboardButton("💵 شحن عبر USDT", callback_data="recharge_usdt"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="back_to_main"))
        bot.edit_message_text("💳 **اختر طريقة الشحن المناسبة:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "recharge_stars":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("إلغاء", callback_data="recharge_menu"))
        msg = bot.send_message(call.message.chat.id, "⭐ **أرسل المبلغ المراد شحنه بالدولار (مثال: 1):**", reply_markup=markup, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_stars_charge)

    elif call.data == "recharge_usdt":
        addr = get_setting('usdt_address') or "غير محدد"
        net = get_setting('usdt_network') or "TRC20"
        
        text = (
            f"💵 **الشحن عن طريق عملة USDT:**\n\n"
            f"🌐 **الشبكة:** `{net}`\n"
            f"🔗 **العنوان:**\n`{addr}`\n\n"
            f"⚠️ **التعليمات:**\n"
            f"1️⃣ قم بتحويل المبلغ المطلوبة إلى العنوان أعلاه.\n"
            f"2️⃣ بعد التحويل قم بإرسال **المبلغ المحول ورقم إثبات التحويل (TxID) أو صورة الإثبات** هنا في المحادثة."
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("رجوع", callback_data="recharge_menu"))
        msg = bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_usdt_user_request)

    elif call.data == "invite":
        # التحقق من حالة نظام الدعوات
        if get_setting('invite_status') == 'off' and not is_admin(user_id):
            bot.answer_callback_query(call.id, "⚠️ نظام الدعوات متوقف حالياً من قبل الإدارة.", show_alert=True)
            return

        bot_username = bot.get_me().username
        invite_link = f"https://t.me/{bot_username}?start={user_id}"
        bot.send_message(
            call.message.chat.id, 
            f"👥 **رابط الدعوة الخاص بك:**\n\n`{invite_link}`\n\n🎁 عند مشاركة الرابط وانضمام أي شخص، ستحصل على **$0.005** مباشرة!", 
            parse_mode="Markdown"
        )

    elif call.data == "terms":
        terms_text = (
            "⚠️ **شروط الاستخدام والخدمة:**\n\n"
            "1. التسليم فوري وآلي بالكامل بعد الشراء.\n"
            "2. يمكنك جلب الكود بالضغط على زر 'جلب الكود' بعد طلب الكود للتطبيق.\n"
            "3. أمامك مهلة 5 دقائق لاستلام الكود وإلا تُسترجع الأموال.\n"
            "4. في حال استلام الكود أسرع بتسجيل الخروج من الجلسة."
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("رجوع", callback_data="back_to_main"))
        bot.edit_message_text(terms_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "language":
        bot.answer_callback_query(call.id, "🌐 اللغة الحالية المعتمدة هي العربية.", show_alert=True)

    elif call.data == "open_admin_panel" or call.data.startswith("admin_"):
        if not is_admin(user_id):
            return
        if call.data == "open_admin_panel":
            send_admin_panel(call.message.chat.id)
        else:
            handle_admin_callbacks(call)

# ================= 6. دوال التحويل والشحن والـ USDT ================= #

def process_transfer(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ صيغة خاطئة! أرسل الآيدي ثم المبلغ بمسافة.", parse_mode="Markdown")
            return

        target_id = int(parts[0])
        amount = float(parts[1])
        sender_id = message.from_user.id
        
        if amount <= 0 or target_id == sender_id:
            bot.reply_to(message, "❌ إدخال غير صالح!")
            return

        sender = get_user_data(sender_id)
        if sender['balance'] < amount:
            bot.reply_to(message, f"❌ رصيدك غير كافٍ! رصيدك: ${sender['balance']:.3f}")
            return

        target = db_query('SELECT user_id FROM users WHERE user_id = ?', (target_id,), fetchone=True)
        if not target:
            bot.reply_to(message, "❌ هذا المستخدم غير مسجل!")
            return

        update_user_field(sender_id, 'balance', sender['balance'] - amount)
        target_data = get_user_data(target_id)
        update_user_field(target_id, 'balance', target_data['balance'] + amount)

        bot.reply_to(message, f"✅ تم تحويل **${amount:.3f}** بنجاح إلى `{target_id}`", parse_mode="Markdown")
        try:
            bot.send_message(target_id, f"🎉 **وصلك تحويل رصيد بقيمة ${amount:.3f}!**\nمن المستخدم: `{sender_id}`", parse_mode="Markdown")
        except Exception:
            pass

    except ValueError:
        bot.reply_to(message, "❌ خطأ في كتابة الأرقام.")

def process_stars_charge(message):
    try:
        amount_usdt = float(message.text.strip())
        if amount_usdt <= 0:
            bot.reply_to(message, "❌ أدخل مبلغ صحيح.")
            return

        stars_required = max(1, int(amount_usdt / STAR_PRICE_USD))
        prices = [LabeledPrice(label="Telegram Stars", amount=stars_required)]
        
        bot.send_invoice(
            chat_id=message.chat.id,
            title=f"شحن رصيد ${amount_usdt}",
            description=f"فاتورة شحن رصيد بقيمة {amount_usdt}$ عبر نجوم تيليجرام ({stars_required} نجمة)",
            invoice_payload=f"stars_recharge_{amount_usdt}",
            provider_token="",
            currency="XTR",
            prices=prices
        )
    except Exception:
        bot.reply_to(message, "❌ أدخل رقم صحيح بالمبلغ بالدولار.")

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    user_id = message.from_user.id
    payload = message.successful_payment.invoice_payload
    if payload.startswith("stars_recharge_"):
        amount_usdt = float(payload.replace("stars_recharge_", ""))
        
        bot.send_message(message.chat.id, f"✅ تم استلام الدفع بنجاح! تم إرسال طلب الشحن إلى الأدمن للموافقة.")
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ موافقة", callback_data=f"admin_approve_charge_{user_id}_{amount_usdt}"),
            InlineKeyboardButton("❌ إلغاء", callback_data=f"admin_decline_charge_{user_id}_{amount_usdt}")
        )
        bot.send_message(
            ADMIN_ID, 
            f"🚨 **طلب شحن جديد عبر النجوم!**\n\n👤 المستخدم: `{user_id}`\n💵 المبلغ: `${amount_usdt:.3f}`", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )

def process_usdt_user_request(message):
    user_id = message.from_user.id
    proof_text = message.text or message.caption or "إثبات تحويل عبر الصورة"
    
    bot.reply_to(message, "✅ **تم استلام تفاصيل التحويل وإرسال الطلب للأدمن للمراجعة والتأكيد.**")
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ قبول وإضافة رصيد", callback_data=f"admin_usdt_approve_{user_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"admin_usdt_decline_{user_id}")
    )
    
    admin_msg = f"🚨 **طلب شحن جديد عبر USDT!**\n\n👤 **المستخدم:** `{user_id}`\n💬 **التفاصيل:**\n{proof_text}"
    
    if message.photo:
        bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=admin_msg, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(ADMIN_ID, admin_msg, reply_markup=markup, parse_mode="Markdown")

# ================= 7. لوحة الأدمن والخيارات ================= #

def send_admin_panel(chat_id):
    m_status = get_setting('maintenance')
    b_status = get_setting('buy_status')
    i_status = get_setting('invite_status')
    
    m_text = "🟢 شغال" if m_status == "off" else "🔴 تحت الصيانة"
    b_text = "🟢 متاح" if b_status == "on" else "🔴 متوقف"
    i_text = "🟢 متاح" if i_status == "on" else "🔴 متوقف"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👤 واجهة المستخدم", callback_data="open_user_panel"))
    markup.row(
        InlineKeyboardButton("➕ إضافة رقم وجلسة", callback_data="admin_add_stock_menu"),
        InlineKeyboardButton("🗑️ حذف رقم من المخزون", callback_data="admin_del_stock")
    )
    markup.row(
        InlineKeyboardButton("➕ إضافة رصيد", callback_data="admin_add_bal"),
        InlineKeyboardButton("➖ خصم رصيد", callback_data="admin_sub_bal")
    )
    markup.row(
        InlineKeyboardButton(f"🛠️ صيانة البوت: {m_text}", callback_data="admin_toggle_maint"),
        InlineKeyboardButton(f"🛒 صيانة الشراء: {b_text}", callback_data="admin_toggle_buy_maint")
    )
    markup.row(
        InlineKeyboardButton(f"👥 نظام الدعوات: {i_text}", callback_data="admin_toggle_invite"),
        InlineKeyboardButton("⚙️ إعدادات USDT", callback_data="admin_usdt_settings")
    )
    markup.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
        InlineKeyboardButton("📢 إذاعة للمستخدمين", callback_data="admin_broadcast")
    )
    markup.add(InlineKeyboardButton("📢 الاشتراك الإجباري", callback_data="admin_forced_sub"))
    
    bot.send_message(chat_id, "⚙️ **لوحة تحكم الأدمن الرئيسي**", reply_markup=markup, parse_mode="Markdown")

def handle_admin_callbacks(call):
    if call.data.startswith("admin_approve_charge_"):
        parts = call.data.split("_")
        uid, amount = int(parts[3]), float(parts[4])
        u = get_user_data(uid)
        update_user_field(uid, 'balance', u['balance'] + amount)
        update_user_field(uid, 'stars_recharged', u['stars_recharged'] + amount)
        
        bot.edit_message_text(f"✅ تم القبول وإضافة `${amount:.3f}` لحساب `{uid}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.send_message(uid, f"🎉 **تمت الموافقة على طلب الشحن!**\nتمت إضافة **${amount:.3f}** إلى رصيدك.", parse_mode="Markdown")
        except Exception:
            pass

    elif call.data.startswith("admin_decline_charge_"):
        parts = call.data.split("_")
        uid = int(parts[3])
        bot.edit_message_text(f"❌ تم رفض طلب الشحن للمستخدم `{uid}`", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    elif call.data.startswith("admin_usdt_approve_"):
        uid = int(call.data.replace("admin_usdt_approve_", ""))
        msg = bot.send_message(call.message.chat.id, f"💵 أدخل **المبلغ بالدولار** الذي تريد إضافته إلى حساب المستخدم `{uid}`:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda m: step_usdt_add_confirm(m, uid))

    elif call.data.startswith("admin_usdt_decline_"):
        uid = int(call.data.replace("admin_usdt_decline_", ""))
        bot.send_message(call.message.chat.id, f"❌ تم رفض طلب شحن USDT للمستخدم `{uid}`")
        try:
            bot.send_message(uid, "❌ **عذراً، تم رفض طلب شحن USDT الخاص بك.**", parse_mode="Markdown")
        except Exception:
            pass

    elif call.data == "admin_toggle_buy_maint":
        curr = get_setting('buy_status')
        new_val = "off" if curr == "on" else "on"
        set_setting('buy_status', new_val)
        bot.answer_callback_query(call.id, f"🛒 حالة قسم الشراء: {new_val}")
        send_admin_panel(call.message.chat.id)

    elif call.data == "admin_toggle_invite":
        curr = get_setting('invite_status')
        new_val = "off" if curr == "on" else "on"
        set_setting('invite_status', new_val)
        bot.answer_callback_query(call.id, f"👥 حالة نظام الدعوات: {new_val}")
        send_admin_panel(call.message.chat.id)

    elif call.data == "admin_usdt_settings":
        addr = get_setting('usdt_address') or "غير محدد"
        net = get_setting('usdt_network') or "TRC20"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📝 تغيير عنوان USDT", callback_data="admin_set_usdt_addr"))
        markup.add(InlineKeyboardButton("🌐 تغيير اسم الشبكة", callback_data="admin_set_usdt_net"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="open_admin_panel"))
        
        bot.edit_message_text(
            f"⚙️ **إعدادات USDT الحالية:**\n\n"
            f"🌐 **الشبكة:** `{net}`\n"
            f"🔗 **العنوان:** `{addr}`", 
            call.message.chat.id, 
            call.message.message_id, 
            reply_markup=markup, 
            parse_mode="Markdown"
        )

    elif call.data == "admin_set_usdt_addr":
        msg = bot.send_message(call.message.chat.id, "🔗 **أرسل عنوان استلام USDT الجديد:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_set_usdt_addr)

    elif call.data == "admin_set_usdt_net":
        msg = bot.send_message(call.message.chat.id, "🌐 **أرسل اسم الشبكة الجديدة** (مثال: `TRC20` أو `BEP20`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_set_usdt_net)

    elif call.data == "admin_add_stock_menu":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🆕 إضافة سلعة جديدة", callback_data="admin_add_stock_new"))
        markup.add(InlineKeyboardButton("📂 إضافة أرقام لسلعة قائمة", callback_data="admin_add_stock_exist"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="open_admin_panel"))
        bot.edit_message_text("📦 **اختر طريقة إضافة المخزون:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data == "admin_add_stock_new":
        msg = bot.send_message(call.message.chat.id, "📦 **أرسل اسم السلعة الجديدة** (مثال: `تليجرام أمريكي سبام 🇺🇸`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_add_stock_name)

    elif call.data == "admin_add_stock_exist":
        existing_items = db_query('SELECT DISTINCT item_name, price FROM stock', fetchall=True)
        if not existing_items:
            bot.answer_callback_query(call.id, "❌ لا توجد سلع مضافة سابقاً!", show_alert=True)
            return
        
        markup = InlineKeyboardMarkup()
        for idx, item in enumerate(existing_items):
            iname, iprice = item[0] or "بدون اسم", float(item[1])
            markup.add(InlineKeyboardButton(f"📂 {iname} (${iprice:.2f})", callback_data=f"admin_add_to_exist_{idx}"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="admin_add_stock_menu"))
        bot.edit_message_text("📂 **اختر السلعة التي تريد إضافة أرقام إليها:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("admin_add_to_exist_"):
        idx = int(call.data.replace("admin_add_to_exist_", ""))
        existing_items = db_query('SELECT DISTINCT item_name, price FROM stock', fetchall=True)
        if idx >= len(existing_items):
            bot.answer_callback_query(call.id, "❌ خطأ في القائمة.", show_alert=True)
            return
        
        item_name, price = existing_items[idx][0], float(existing_items[idx][1])
        msg = bot.send_message(
            call.message.chat.id, 
            f"📦 **السلعة المختارة:** `{item_name}` (${price:.2f})\n\n📱 أرسل **رقم الهاتف** مع المفتاح الدولي (مثال: `+9647700000000`):", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, lambda m: step_add_stock_phone(m, item_name, price))

    elif call.data == "admin_del_stock":
        items = db_query('SELECT id, item_name, phone, price FROM stock WHERE is_sold = 0', fetchall=True)
        if not items:
            bot.send_message(call.message.chat.id, "❌ لا توجد أرقام متاحة بالمخزون.")
            return
        markup = InlineKeyboardMarkup()
        for item in items:
            item_id, item_name, phone_num, price = item[0], item[1] or "بدون اسم", item[2], item[3]
            markup.add(InlineKeyboardButton(f"❌ حذف: {item_name} | {phone_num} (${price})", callback_data=f"admin_del_stock_exec_{item_id}"))
        markup.add(InlineKeyboardButton("رجوع", callback_data="open_admin_panel"))
        bot.edit_message_text("🗑️ **اختر السلعة المراد حذفها نهائياً:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("admin_del_stock_exec_"):
        stock_id = int(call.data.replace("admin_del_stock_exec_", ""))
        item = db_query('SELECT phone FROM stock WHERE id = ?', (stock_id,), fetchone=True)
        if item:
            delete_session_file(item[0])
        db_query('DELETE FROM stock WHERE id = ?', (stock_id,), commit=True)
        bot.answer_callback_query(call.id, "✅ تم حذف الرقم بنجاح!")
        send_admin_panel(call.message.chat.id)

    elif call.data == "admin_add_bal":
        msg = bot.send_message(call.message.chat.id, "➕ أرسل **آيدي المستخدم** و **المبلغ** (مثال: `12345678 1.5`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_add_bal)

    elif call.data == "admin_sub_bal":
        msg = bot.send_message(call.message.chat.id, "➖ أرسل **آيدي المستخدم** و **المبلغ** (مثال: `12345678 1.5`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_sub_bal)

    elif call.data == "admin_toggle_maint":
        curr = get_setting('maintenance')
        new_val = "on" if curr == "off" else "off"
        set_setting('maintenance', new_val)
        bot.answer_callback_query(call.id, f"🛠️ وضع الصيانة: {new_val}")
        send_admin_panel(call.message.chat.id)

    elif call.data == "admin_stats":
        total_users = db_query('SELECT COUNT(*) FROM users', fetchone=True)[0]
        total_stock = db_query('SELECT COUNT(*) FROM stock WHERE is_sold = 0', fetchone=True)[0]
        sold_stock = db_query('SELECT COUNT(*) FROM stock WHERE is_sold = 1', fetchone=True)[0]
        
        stats_msg = (
            f"📊 **إحصائيات البوت الكاملة:**\n\n"
            f"👥 **إجمالي المستخدمين:** `{total_users}`\n"
            f"📱 **الأرقام المتاحة بالمخزون:** `{total_stock}`\n"
            f"✅ **الأرقام المباعة:** `{sold_stock}`"
        )
        bot.send_message(call.message.chat.id, stats_msg, parse_mode="Markdown")

    elif call.data == "admin_broadcast":
        msg = bot.send_message(call.message.chat.id, "📢 **قم بإرسال نص الإذاعة:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, step_broadcast)

    elif call.data == "admin_forced_sub":
        curr_chan = get_setting('forced_channel') or "غير مفعل"
        msg = bot.send_message(
            call.message.chat.id, 
            f"📢 **إعداد الاشتراك الإجباري**\n\nالقناة الحالية: `{curr_chan}`\nأرسل **يوزر القناة** (مثال: `@mychannel`)\nأو أرسل كلمة `off` لإلغاء التفعيل:", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, step_forced_sub)

# --- خطوات تحديث بيانات USDT والتحكم ---

def step_set_usdt_addr(message):
    new_addr = message.text.strip()
    set_setting('usdt_address', new_addr)
    bot.reply_to(message, f"✅ **تم حفظ عنوان USDT الجديد بنجاح:**\n`{new_addr}`", parse_mode="Markdown")

def step_set_usdt_net(message):
    new_net = message.text.strip()
    set_setting('usdt_network', new_net)
    bot.reply_to(message, f"✅ **تم تغيير شبكة USDT إلى:** `{new_net}`", parse_mode="Markdown")

def step_usdt_add_confirm(message, target_uid):
    try:
        amt = float(message.text.strip())
        u = get_user_data(target_uid)
        update_user_field(target_uid, 'balance', u['balance'] + amt)
        bot.reply_to(message, f"✅ تم إضافة `${amt:.3f}` إلى حساب المستخدم `{target_uid}` بنجاح!", parse_mode="Markdown")
        try:
            bot.send_message(target_uid, f"🎉 **تم قبول طلب شحن USDT الخاص بك!**\nتمت إضافة **${amt:.3f}** إلى رصيدك.", parse_mode="Markdown")
        except Exception:
            pass
    except ValueError:
        bot.reply_to(message, "❌ يرجى إدخال مبلغ صحيح.")

# --- خطوات تسجيل الرقم واستخراج الجلسات ---

def step_add_stock_name(message):
    item_name = message.text.strip()
    msg = bot.send_message(
        message.chat.id, 
        f"📦 **السلعة:** {item_name}\n\n💵 أرسل **سعر السلعة بالدولار** (مثال: `1.5`):", 
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, lambda m: step_add_stock_price(m, item_name))

def step_add_stock_price(message, item_name):
    try:
        price = float(message.text.strip())
        msg = bot.send_message(
            message.chat.id, 
            f"📦 **السلعة:** {item_name}\n💵 **السعر:** ${price:.2f}\n\n📱 أرسل **رقم الهاتف** مع المفتاح الدولي (مثال: `+9647700000000`):", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, lambda m: step_add_stock_phone(m, item_name, price))
    except ValueError:
        bot.reply_to(message, "❌ سعر غير صالح!")

def step_add_stock_phone(message, item_name, price):
    phone = message.text.strip()
    admin_id = message.from_user.id

    bot.send_message(message.chat.id, "⏳ **جاري الاتصال بتليجرام وإرسال كود التحقق للرقم...**", parse_mode="Markdown")

    client = TelegramClient(StringSession(), API_ID, API_HASH)

    try:
        client.connect()
        sent_code = client.send_code_request(phone)
        session_str = client.session.save()
        client.disconnect()

        admin_login_data[admin_id] = {
            'item_name': item_name,
            'session_str': session_str,
            'phone': phone,
            'price': price,
            'phone_code_hash': sent_code.phone_code_hash
        }

        msg = bot.send_message(
            message.chat.id, 
            f"📩 تم إرسال كود التحقق للرقم `{phone}`!\n\nالآن أرسل **كود التحقق** (مثال: `12345`):", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, step_add_stock_code)

    except Exception as e:
        if client.is_connected():
            client.disconnect()
        bot.reply_to(message, f"❌ حدث خطأ أثناء إرسال الكود: {str(e)}")

def step_add_stock_code(message):
    admin_id = message.from_user.id
    data = admin_login_data.get(admin_id)

    if not data:
        bot.reply_to(message, "❌ انتهت الجلسة، أعد المحاولة.")
        return

    code = message.text.strip()
    item_name, session_str, phone, price, phone_code_hash = data['item_name'], data['session_str'], data['phone'], data['price'], data['phone_code_hash']

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)

    try:
        client.connect()
        client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        final_session_str = client.session.save()
        client.disconnect()

        save_session_to_file(phone, final_session_str)
        created_at = datetime.now().isoformat()
        db_query('INSERT INTO stock (item_name, phone, session_str, price, created_at) VALUES (?, ?, ?, ?, ?)', (item_name, phone, final_session_str, price, created_at), commit=True)

        del admin_login_data[admin_id]

        bot.reply_to(
            message, 
            f"✅ **تم تسجيل الجلسة وحفظها بنجاح!**\n\n"
            f"📦 **السلعة:** `{item_name}`\n"
            f"📱 **الرقم:** `{phone}`\n"
            f"💵 **السعر:** `${price:.2f}`", 
            parse_mode="Markdown"
        )

    except SessionPasswordNeededError:
        data['session_str'] = client.session.save()
        if client.is_connected():
            client.disconnect()
        msg = bot.send_message(
            message.chat.id, 
            "🔐 **هذا الحساب محمي بكلمة سر (2FA).**\n\nيرجى إرسال **كلمة سر الحساب**: ", 
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, step_add_stock_2fa)

    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        bot.reply_to(message, "❌ الكود غير صحيح أو انتهت صلاحيته!")
        if client.is_connected():
            client.disconnect()
        if admin_id in admin_login_data:
            del admin_login_data[admin_id]

    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ: {str(e)}")
        if client.is_connected():
            client.disconnect()
        if admin_id in admin_login_data:
            del admin_login_data[admin_id]

def step_add_stock_2fa(message):
    admin_id = message.from_user.id
    data = admin_login_data.get(admin_id)

    if not data:
        bot.reply_to(message, "❌ انتهت الجلسة.")
        return

    password = message.text.strip()
    item_name, session_str, phone, price = data['item_name'], data['session_str'], data['phone'], data['price']

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)

    try:
        client.connect()
        client.sign_in(password=password)
        final_session_str = client.session.save()
        client.disconnect()

        save_session_to_file(phone, final_session_str)
        created_at = datetime.now().isoformat()
        db_query('INSERT INTO stock (item_name, phone, session_str, price, created_at) VALUES (?, ?, ?, ?, ?)', (item_name, phone, final_session_str, price, created_at), commit=True)

        del admin_login_data[admin_id]

        bot.reply_to(
            message, 
            f"✅ **تم تسجيل الجلسة وحفظها بنجاح!**\n\n"
            f"📦 **السلعة:** `{item_name}`\n"
            f"📱 **الرقم:** `{phone}`\n"
            f"💵 **السعر:** `${price:.2f}`", 
            parse_mode="Markdown"
        )

    except Exception as e:
        bot.reply_to(message, f"❌ كلمة السر غير صحيحة: {str(e)}")
        if client.is_connected():
            client.disconnect()
        if admin_id in admin_login_data:
            del admin_login_data[admin_id]

# --- باقي خطوات لوحة التحكم ---

def step_add_bal(message):
    try:
        parts = message.text.strip().split()
        uid, amt = int(parts[0]), float(parts[1])
        u = get_user_data(uid)
        update_user_field(uid, 'balance', u['balance'] + amt)
        bot.reply_to(message, f"✅ تم إضافة `${amt:.3f}` لحساب `{uid}`", parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, "❌ صيغة خاطئة!")

def step_sub_bal(message):
    try:
        parts = message.text.strip().split()
        uid, amt = int(parts[0]), float(parts[1])
        u = get_user_data(uid)
        update_user_field(uid, 'balance', max(0.0, u['balance'] - amt))
        bot.reply_to(message, f"✅ تم خصم `${amt:.3f}` من حساب `{uid}`", parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, "❌ صيغة خاطئة!")

def step_broadcast(message):
    users = db_query('SELECT user_id FROM users', fetchall=True)
    success, failed = 0, 0
    bot.send_message(message.chat.id, "⏳ جاري إرسال الإذاعة...")
    
    for u in users:
        try:
            bot.copy_message(u[0], message.chat.id, message.message_id)
            success += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
            
    bot.send_message(
        message.chat.id, 
        f"✅ **اكتملت الإذاعة!**\n\n🎯 نجح: `{success}`\n❌ فشل: `{failed}`", 
        parse_mode="Markdown"
    )

def step_forced_sub(message):
    val = message.text.strip()
    if val.lower() == 'off':
        set_setting('forced_channel', '')
        bot.reply_to(message, "✅ تم إلغاء الاشتراك الإجباري.")
    else:
        set_setting('forced_channel', val)
        bot.reply_to(message, f"✅ تم ضبط القناة: `{val}`", parse_mode="Markdown")

# ================= 8. تشغيل البوت ================= #

print("🔄 جاري مزامنة الجلسات في مجلد account...")
sync_sessions_to_files()

print("🚀 البوت يعمل الآن بنجاح مع زر تحكم نظام الدعوات...")
bot.infinity_polling(skip_pending=True)
