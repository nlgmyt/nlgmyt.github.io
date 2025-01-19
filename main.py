import logging
import os
from datetime import datetime, timedelta
import re
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json  # Import thư viện json để làm việc với dữ liệu JSON
from flask import Flask, request

# --- CẤU HÌNH ---
class Config:
    """Lớp chứa các cấu hình của bot."""
    BOT_TOKEN = "7684510871:AAHmPcT0KI5VqIQ_DE7jdaQngaL_TWqINCw"  # Thay bằng token bot của bạn
    ADMIN_CHAT_ID = "5049353267"  # Thay bằng ID chat của admin
    # GOOGLE_JSON_KEY_PATH = "/storage/emulated/0/Download/thu-chi-tele-446215-dd9acc542272.json" # Xóa dòng này
    GOOGLE_SHEET_NAME = "ThuChiData"
    GOOGLE_USER_MANAGEMENT_WORKSHEET_NAME = "UserManagement"
    GOOGLE_MESSAGES_LOG_WORKSHEET_NAME = "MessagesLog" # Tên worksheet để lưu log tin nhắn

     # Ánh xạ cột trong worksheet UserManagement
    USER_COLUMNS = {
        'chat_id': 0,
        'start_time': 1,
        'expiry_date': 2,
        'status': 3,
    }

    @classmethod
    def validate(cls):
        """Kiểm tra tính hợp lệ của các biến cấu hình."""
        if not all([cls.BOT_TOKEN, cls.ADMIN_CHAT_ID]): # Xóa kiểm tra GOOGLE_JSON_KEY_PATH
            raise ValueError("Không tìm thấy tất cả các biến môi trường cần thiết. Hãy kiểm tra file .env.")
        
        if not cls.ADMIN_CHAT_ID.isdigit():
            raise ValueError("ADMIN_CHAT_ID phải là một số nguyên.")
        
        if not all([isinstance(index, int) for index in cls.USER_COLUMNS.values()]):
            raise ValueError("Giá trị trong USER_COLUMNS dictionary phải là số nguyên.")
# Tải cấu hình
try:
    config = Config()
    Config.validate()
except ValueError as e:
    print(f"Lỗi cấu hình: {e}")
    exit(1)

# --- CÁC LOẠI LỖI ---
class BotError(Exception):
    """Lỗi chung của bot."""
    pass

class GoogleSheetError(BotError):
    """Lỗi liên quan đến Google Sheets."""
    pass

class InvalidInputError(BotError):
    """Lỗi đầu vào không hợp lệ."""
    pass

# --- XỬ LÝ GOOGLE SHEETS ---
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
SHEET_LINK_REGEX = r'https:\/\/docs\.google\.com\/spreadsheets\/d\/([a-zA-Z0-9-_]+)' # Regex để kiểm tra link sheet

class GoogleSheetsHandler:
    def __init__(self):
        try:
            json_key = os.getenv("GOOGLE_JSON_KEY")
            if not json_key:
                raise ValueError("Biến môi trường GOOGLE_JSON_KEY không được cấu hình.")
            
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
            self.client = gspread.authorize(creds)
            self.user_management_sheet = self.client.open(config.GOOGLE_SHEET_NAME).worksheet(config.GOOGLE_USER_MANAGEMENT_WORKSHEET_NAME)
            self.messages_log_sheet = self.client.open(config.GOOGLE_SHEET_NAME).worksheet(config.GOOGLE_MESSAGES_LOG_WORKSHEET_NAME) # Khởi tạo worksheet log tin nhắn
            logger.info("Kết nối đến Google Sheets thành công.")
        except Exception as e:
            logger.error(f"Lỗi kết nối đến Google Sheets: {e}")
            raise GoogleSheetError(f"Lỗi kết nối đến Google Sheets: {e}")

    def _get_user_row(self, chat_id):
         try:
            users = self.user_management_sheet.get_all_records()
            for index, user in enumerate(users):
                if user['ChatID'] == str(chat_id) and user.get('SheetLink'):
                    return index + 2
            return None
         except Exception as e:
            logger.error(f"Lỗi khi lấy thông tin người dùng từ sheet: {e}")
            raise GoogleSheetError(f"Lỗi khi lấy thông tin người dùng từ sheet: {e}")

    def get_user_from_sheet(self, chat_id):
        try:
            users = self.user_management_sheet.get_all_records()            
            for user in users:
                if str(user['ChatID']) == str(chat_id):
                    return user
            return None
        except Exception as e:
            logger.error(f"Lỗi khi lấy thông tin người dùng từ sheet: {e}")
            raise GoogleSheetError(f"Lỗi khi lấy thông tin người dùng từ sheet: {e}")
    
    def update_user_sheet(self, chat_id, start_time=None, expiry_date=None, status=None):
        try:
            user_row = self._get_user_row(chat_id)

            values_to_update = {}
            if start_time:
                values_to_update[config.USER_COLUMNS['start_time']] = start_time
            if expiry_date:
                values_to_update[config.USER_COLUMNS['expiry_date']] = expiry_date
            if status:
                values_to_update[config.USER_COLUMNS['status']] = status
            
            if user_row:
                # Cập nhật thông tin người dùng đã có
                for col, value in values_to_update.items():
                    self.user_management_sheet.update_cell(user_row, col + 1, value)

                logger.info(f"Đã cập nhật thông tin người dùng {chat_id} trong sheet. Cập nhật: {values_to_update}")
            else:
                # Thêm người dùng mới
                new_row = [str(chat_id), start_time, expiry_date, status]
                self.user_management_sheet.append_row(new_row)
                logger.info(f"Đã thêm người dùng mới {chat_id} vào sheet: {new_row}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi cập nhật sheet: {e}")
            raise GoogleSheetError(f"Lỗi khi cập nhật sheet: {e}")
    
    def get_user_sheet_link(self, chat_id):
         try:
             user = self.get_user_from_sheet(chat_id)
             if user and user.get('SheetLink'):
                return user['SheetLink']
             return None
         except Exception as e:
            logger.error(f"Lỗi khi lấy link sheet user: {e}")
            raise GoogleSheetError(f"Lỗi khi lấy link sheet user: {e}")
    
    def update_user_sheet_link(self, chat_id, sheet_link=None):
         try:
            users = self.user_management_sheet.get_all_records()
            user_row = next(
                (index + 2 for index, user in enumerate(users) if str(user['ChatID']) == str(chat_id)), None
             )
            
            if user_row:
                self.user_management_sheet.update_cell(user_row, 5, sheet_link)  # Giả sử cột 5 là `SheetLink`
                logger.info(f"Đã cập nhật link sheet của người dùng {chat_id}: {sheet_link}")
            else:
                # Chỉ thêm dòng mới nếu không có user nào
                existing_user = any(user['ChatID'] == str(chat_id) for user in users)
                if not existing_user:
                    new_row = [str(chat_id), None, None, None, sheet_link]
                    self.user_management_sheet.append_row(new_row)
                    logger.info(f"Đã thêm link sheet mới cho user {chat_id}: {new_row}")
                else:
                    raise GoogleSheetError("Không thể tìm thấy hàng hợp lệ để cập nhật.")
            return True
         except Exception as e:
            logger.error(f"Lỗi khi cập nhật sheet link: {e}")
            raise GoogleSheetError(f"Lỗi khi cập nhật sheet link: {e}")
            
    def validate_sheet_link(self, sheet_link):
         try:
            # Kiểm tra link có phải là dạng URL của Google Sheets
            if "docs.google.com/spreadsheets" not in sheet_link:
                return False

            # Thử mở sheet bằng link để kiểm tra tính hợp lệ
            self.client.open_by_url(sheet_link)
            return True
         except Exception as e:
            return False

    def add_expense(self, chat_id, description, amount, sheet_link):
        try:
            if not self.validate_sheet_link(sheet_link):
                raise GoogleSheetError("Link sheet chi tiêu không hợp lệ.")
            user_sheet = self.client.open_by_url(sheet_link).sheet1
            now = datetime.now()
            date_string = now.strftime('%Y-%m-%d %H:%M:%S')
            new_row = [date_string, description, amount]
            user_sheet.append_row(new_row)
            logger.info(f"Đã thêm chi tiêu cho người dùng {chat_id}: {new_row}")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi thêm chi tiêu: {e}")
            raise GoogleSheetError(f"Lỗi khi thêm chi tiêu: {e}")
    
    def log_message_to_sheet(self, chat_id, message_text, message_count):
        """Lưu thông tin tin nhắn vào Google Sheet."""
        try:
            # Sử dụng worksheet messages_log_sheet đã được khởi tạo
            new_row = [str(chat_id), message_text, message_count]
            self.messages_log_sheet.append_row(new_row)
            logger.info(f"Đã lưu tin nhắn của {chat_id} vào Google Sheet: {new_row}")
        except Exception as e:
             logger.error(f"Lỗi khi ghi log tin nhắn vào Google Sheet: {e}")
             raise GoogleSheetError(f"Lỗi khi ghi log tin nhắn vào Google Sheet: {e}")

# --- TELEGRAM BOT HANDLER ---
logger = logging.getLogger(__name__)
DATE_FORMAT = '%Y-%m-%d'
DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'
TIME_WINDOW_MINUTES = 1 # Khoảng thời gian cho việc chống spam
SPAM_LIMIT = 5 # Số tin nhắn tối đa để chống spam

# Lưu thông tin phiên của người dùng
user_sessions = {}
user_message_times = {} # Lưu thời gian gửi tin nhắn để chống spam

class TelegramBotHandler:
    def __init__(self, bot):
        # Không khởi tạo bot ở đây nữa mà sử dụng bot từ Flask
        self.bot = bot
        self.app = Application.builder().token(config.BOT_TOKEN).build()
        self.sheets_handler = GoogleSheetsHandler()
        self._register_handlers()
        logger.info("Bot Telegram đã được khởi tạo.")

    def _register_handlers(self):
         self.app.add_handler(CommandHandler('start', self.start))
         self.app.add_handler(CommandHandler('recharge', self.recharge))
         self.app.add_handler(CommandHandler('paid', self.paid))
         self.app.add_handler(CommandHandler('activate', self.activate))
         self.app.add_handler(CommandHandler('help', self.help))
         self.app.add_handler(CommandHandler('getid', self.get_id))
         self.app.add_handler(CommandHandler('set_sheet', self.set_sheet))  # Thêm handler cho lệnh set_sheet
         self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def start(self, update: Update, context):
        chat_id = update.effective_chat.id
        now = datetime.now().strftime(DATETIME_FORMAT)
        user = self.sheets_handler.get_user_from_sheet(chat_id)
        
        if user and user.get('ExpiryDate'):
             expiry_date = user.get('ExpiryDate')
             if expiry_date:
                 expiry_date = datetime.strptime(expiry_date, DATE_FORMAT).date()
                 if datetime.now().date() > expiry_date:
                      await context.bot.send_message(
                        chat_id=chat_id,
                        text="Tài khoản của bạn đã hết hạn. Vui lòng nạp tiền để tiếp tục sử dụng. /"
                         )
                      return
             await context.bot.send_message(chat_id=chat_id, text="Chào mừng bạn quay lại với Tool! /help để biết thêm thông tin")
        elif not user:
            if self.sheets_handler.update_user_sheet(chat_id, start_time=now, status="Trial", expiry_date=(datetime.now() + timedelta(hours=2)).strftime(DATE_FORMAT)):
                await context.bot.send_message(chat_id=chat_id, text="Công cụ Quản Lí Thu Chi Xin Chào!\n \n  /help để biết thêm! \n\n\n Bạn có 24 giờ dùng thử miễn phí!")
            else:
                await context.bot.send_message(chat_id=chat_id, text="Có lỗi xảy ra, vui lòng thử lại sau.")
            
    async def recharge(self, update: Update, context):
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
              "Bảng giá: \n 10.000₫/tháng \n 18.000₫ \n 27.000₫"
                "Để gia hạn tài khoản, vui lòng thanh toán qua:\n\n"
                "Ngân hàng\n"
                "• Ngân hàng: Mb Bank\n\n"
                "• Tên tài khoản: Nguyễn Huỳnh Hoàng Long\n"
                "• Số tài khoản: 0772144548\n"
                "• Nội dung chuyển khoản: id telegram \n  /getid để lấy id"
                "Sau khi thanh toán, hãy sử dụng lệnh /paid kèm mã giao dịch hoặc thông tin chuyển khoản để chúng tôi kích hoạt tài khoản cho bạn.\n Nếu gặp vấn đề hãy liên hệ @harrynoblenlgmyt"
            ),
        )

    async def paid(self, update: Update, context):
        chat_id = update.effective_chat.id
        user_message = ' '.join(context.args).strip()

        if not user_message:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Vui lòng nhập thông tin thanh toán. Ví dụ: /paid Mã giao dịch ABC12345."
            )
            return

        await context.bot.send_message(
            chat_id=config.ADMIN_CHAT_ID,
            text=(
                f"Người dùng {chat_id} đã gửi thông báo thanh toán:\n"
                f"{user_message}\n\n"
                f"Vui lòng kiểm tra giao dịch và cập nhật trạng thái tài khoản."
            ),
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="Cảm ơn bạn đã thanh toán! Chúng tôi sẽ xác nhận và kích hoạt tài khoản trong thời gian sớm nhất."
        )

    async def activate(self, update: Update, context):
        chat_id = update.effective_chat.id

        if str(chat_id) != config.ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=chat_id, text="Bạn không có quyền sử dụng lệnh này.")
            return

        try:
            if len(context.args) != 2:
                raise InvalidInputError("Lệnh không hợp lệ. Ví dụ: /activate <chat_id> <yyyy-mm-dd>.")

            target_chat_id = context.args[0].strip()
            expiry_date_str = context.args[1].strip()

            expiry_date = datetime.strptime(expiry_date_str, DATE_FORMAT).strftime(DATE_FORMAT)

            # Logic kích hoạt tài khoản (nếu cần)
            await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=f"Tài khoản của bạn đã được gia hạn đến {expiry_date}."
                )
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Đã kích hoạt tài khoản cho người dùng {target_chat_id}."
            )
            self.sheets_handler.update_user_sheet(target_chat_id, expiry_date=expiry_date)
        except ValueError:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Định dạng ngày không hợp lệ. Vui lòng sử dụng định dạng yyyy-mm-dd."
            )
        except BotError as e:
            logger.error(f"Lỗi trong lệnh activate: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Lỗi khi kích hoạt tài khoản. Vui lòng kiểm tra thông tin và thử lại. {e}"
            )
        except Exception as e:
             logger.error(f"Lỗi không xác định trong lệnh activate: {e}")
             await context.bot.send_message(
                chat_id=chat_id,
                text=f"Có lỗi không xác định xảy ra. Vui lòng thử lại sau. {e}"
            )

    async def set_sheet(self, update: Update, context):
        chat_id = update.effective_chat.id
        try:
            if len(context.args) != 1:
                raise InvalidInputError("Lệnh không hợp lệ. Ví dụ: /set_sheet <link>")

            sheet_link = context.args[0].strip()
            if not self.sheets_handler.validate_sheet_link(sheet_link):
                raise InvalidInputError("Link sheet không hợp lệ.")

            # Lưu link Google Sheet vào sheet user của admin
            
            if self.sheets_handler.update_user_sheet_link(chat_id, sheet_link=sheet_link):
                 await context.bot.send_message(chat_id=chat_id, text="Đã lưu link Google Sheet thành công!\n\nHướng dẫn nhập dữ liệu:\n- Chỉ cần nhập tin nhắn theo cú pháp: <nội dung> <số tiền>.\n- Ví dụ:\n  \"Mua cà phê 100k\"\n  \"Ăn trưa 50000\"\n  \"Đóng tiền nhà 1.5tr\"\nDữ liệu sẽ được tự động lưu vào Google Sheet của bạn.")
            else:
                await context.bot.send_message(chat_id=chat_id, text="Có lỗi xảy ra, vui lòng thử lại sau.")
        except BotError as e:
            logger.error(f"Lỗi trong lệnh set_sheet: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"Lỗi: {e}")
        except Exception as e:
            logger.error(f"Lỗi không xác định trong lệnh set_sheet: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"Có lỗi xảy ra: {e}")

    async def help(self, update: Update, context):
         help_message = (
           
    "✨ **Danh sách lệnh hỗ trợ** ✨\n\n"
    
    
        "📑 **Quản lý chi tiêu:**\n"
    "/set_sheet <link> - Đặt link Google Sheet để lưu chi tiêu của bạn.\n ❌☠️ Lưu ý: Link sheet phải đặt công khai và được phép chỉnh sửa!"
    "  - Ví dụ: Nhập 'đi ăn 100k ' hoặc 'mua sách 200', bot sẽ tự động thêm vào sheet chi tiêu.\n\n"
    
    
    "⚙️ **Lệnh cơ bản:**\n"
    "/start - Bắt đầu sử dụng bot.\n"
    "/getid - Nhận CHAT ID để thanh toán.\n"
    "/recharge - Xem thông tin thanh toán để gia hạn tài khoản.\n"
    "/paid <mã giao dịch> - Gửi thông tin thanh toán sau khi bạn đã nạp tiền.\n\n"


    
    "⏳ **Thời gian sử dụng miễn phí:**\n"
    "Bạn có 24 giờ sử dụng miễn phí. Sau khi hết thời gian, vui lòng thanh toán để tiếp tục sử dụng bot.\n\n"
    
    "🔒 **Lệnh cho Admin:**\n"
    "/activate <chat_id> <yyyy-mm-dd> - Kích hoạt tài khoản người dùng.\n\n"
    
    "=====================================\n"
    "Nếu gặp vấn đề, vui lòng liên hệ hỗ trợ qua @harrynoblenlgmyt."


        )
         await context.bot.send_message(chat_id=update.effective_chat.id, text=help_message)

    async def get_id(self, update: Update, context):
        try:
            chat_id = update.effective_chat.id
            await context.bot.send_message(chat_id=chat_id, text=f"Chat ID của bạn là: {chat_id}")
        except Exception as e:
            logger.error(f"Lỗi trong lệnh getid: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Lỗi: {e}")
    
    async def handle_message(self, update: Update, context):
         chat_id = update.effective_chat.id
         message_text = update.message.text.strip()
         try:
             now = datetime.now()
             user = self.sheets_handler.get_user_from_sheet(chat_id)
             if user:
                expiry_date = user.get('ExpiryDate')
                if expiry_date:
                   expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date()
                   if datetime.now().date() > expiry_date:
                        await context.bot.send_message(
                             chat_id=chat_id,
                            text="Tài khoản của bạn đã hết hạn. Vui lòng nạp tiền để tiếp tục sử dụng. /recharge"
                        )
                        return
             
             # --- Bắt đầu ghi log tin nhắn ---
             if chat_id not in user_message_times:
                 user_message_times[chat_id] = []
             user_message_times[chat_id].append(now) # Thêm thời gian hiện tại
              # Xóa các timestamp cũ hơn thời gian giới hạn
             user_message_times[chat_id] = [
                  timestamp for timestamp in user_message_times[chat_id]
                    if now - timestamp < timedelta(minutes=TIME_WINDOW_MINUTES)
                ]
             message_count = len(user_message_times[chat_id])
             
             try:
               self.sheets_handler.log_message_to_sheet(chat_id, message_text, message_count)
             except GoogleSheetError as e:
                logger.error(f"Error logging message to sheet: {e}")
                await context.bot.send_message(
                   chat_id=chat_id,
                   text="Có lỗi khi lưu tin nhắn vào sheet log. Vui lòng thử lại sau."
                 )
                return
             # Kiểm tra spam
             if message_count > SPAM_LIMIT:
                 await context.bot.send_message(
                        chat_id=chat_id,
                        text="Bạn đã gửi quá nhiều tin nhắn trong thời gian ngắn. Bạn bị đánh dấu là spammer."
                    )
                 return
             
             # --- Kết thúc ghi log tin nhắn ---

             # Kiểm tra nếu tin nhắn khớp với định dạng "nội dung số_tiền"
             match = re.match(r"(.*)\s+([\d\.]+k?|[\d\.]+tr?)", message_text, re.IGNORECASE) # Thay regex ở đây
             if match:
                description = match.group(1).strip()
                amount_str = match.group(2)
                
                try:
                    amount = self.normalize_amount(amount_str)
                    sheet_link = self.sheets_handler.get_user_sheet_link(chat_id)
                    if not sheet_link:
                         await context.bot.send_message(
                                chat_id=chat_id,
                                text="Vui lòng sử dụng lệnh /set_sheet <link_sheet_của_bạn> để thiết lập link sheet chi tiêu trước khi thêm chi tiêu."
                                 )
                         return
                    
                    if self.sheets_handler.add_expense(chat_id, description, amount, sheet_link):
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Đã thêm:\nNội dung: {description}\nSố tiền: {amount}"
                        )
                        return
                    else:
                         await context.bot.send_message(
                            chat_id=chat_id,
                            text="Có lỗi xảy ra khi thêm chi tiêu, vui lòng thử lại sau."
                         )
                         return
                except ValueError:
                         await context.bot.send_message(
                            chat_id=chat_id,
                            text="Định dạng chi tiêu không hợp lệ. Vui lòng nhập theo định dạng: đi ăn 100k hoặc mua sách 200."
                         )
                         return
             else:
                  await context.bot.send_message(
                    chat_id=chat_id,
                    text="Tin nhắn không hợp lệ. Vui lòng nhập theo định dạng: <nội dung> <số tiền>, ví dụ: đi ăn 100k."
                         )
             
         except Exception as e:
             logger.error(f"Lỗi không xác định trong handle_message: {e}")
             await context.bot.send_message(
                chat_id=chat_id,
                text=f"Có lỗi không xác định xảy ra. Vui lòng thử lại sau. {e}"
            )

    def normalize_amount(self, amount_str):
        amount_str = amount_str.lower()
        if amount_str.endswith('k'):
           amount = float(amount_str[:-1]) * 1000
        elif amount_str.endswith('tr'):
          amount = float(amount_str[:-2]) * 1000000
        elif "." in amount_str or "," in amount_str:
          amount = float(amount_str.replace(",", "."))
        else:
            amount = float(amount_str)
        return amount
        
# --- ỨNG DỤNG FLASK ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("Webhook data received:", data)
    if data:
        try:
            update = Update.de_json(data, bot)
            if update.message:
                telegram_bot_handler.app.process_update(update)
            else:
                logger.warning("Webhook received non-message update")
        except Exception as e:
            logger.error(f"Error processing webhook data: {e}")
    return "OK"

# --- MAIN ---
# Cấu hình logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    """Khởi tạo và chạy bot."""
    try:
        # Khởi tạo Bot từ Flask để không bị xung đột
        bot = Bot(token=config.BOT_TOKEN)
        telegram_bot_handler = TelegramBotHandler(bot)

        # Chạy bot sử dụng webhook
        port = int(os.environ.get("PORT", 10000))
        app.run(host='0.0.0.0', port=port)
      
    except Exception as e:
        logger.error(f"Lỗi trong quá trình khởi chạy bot: {e}")