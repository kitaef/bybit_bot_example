import sys
import time

import asyncio
import aiohttp
from telebot.async_telebot import AsyncTeleBot
from telebot import ExceptionHandler
from loguru import logger
from typing import List

from database import SessionLocal, Orders
from authorise import gen_signature
from config import API_KEY, SECRET_KEY, RECV_WINDOW, \
    BASE_URL, TG_TOKEN, LOG_LEVEL

# Keys and their types for fetching from bybit responses
KEYS = {'symbol': str, 'side': str, 'avgPrice': float, 'markPrice': float,
        'size': float, 'positionValue': float, 'unrealisedPnl': float,
        'createdTime': float, 'updatedTime': int}

# SL = [-0.1, 0.05, 0.1]  # Test SL values
# TRIGGERS = [-0.05, 0.1, 0.11]  # Test triggers
STOPOUT = [-8, 1, 5]  # SL values
TRIGGERS = [-5, 5, 10]  # Triggers

logger.add("bybit_log.log", level=LOG_LEVEL,
           colorize=False, backtrace=True, diagnose=True)


class BotException(ExceptionHandler):
    """Handles exceptions raised by TelegramBot"""

    def handle(self, exception):
        logger.error(exception)


bot = AsyncTeleBot(TG_TOKEN, exception_handler=BotException())


@bot.message_handler(commands=['start'])
async def start_message(message):
    msg = """Hello. Meet the ByBit positions follower bot.
            It automatically opens \"Buy\" positions for requested trading instruments at ByBit. Then the bot follows
            every position according to the next logic:
            1) If the Pnl drops to -5%, the bot sends the limit order to sell the position at the price it was opened.
            2) If the Pnl drops to -8% (initial stop loss price) and there is already a limit order opened,
            the bot cancels this order and opens the reduce order  to sell the position by market price.
            3) If the Pnl rises by 5%, the bot changes the value for stop loss from -8% to 1%,
            so that if the Pnl drops back to this level, the position will be closed with 1% profit
            4) If the Pnl rises by 10%, the bot changes the value for stop loss to 5%
            
            The commands supported:
            1) /OPEN BTCUSDT ETHUSDT ADAUSDT
            Open and start following the positions for the tokens listed
            2) /CLOSE BTCUSDT ETHUSDT ADAUSDT
            Close and stop following the positions for the tokens listed
            3) /SHOW
            Show all open positions (the settle coin is USDT)
            4) /GETLOG
            Download the full application log file"""
    await bot.send_message(message.chat.id, msg)


@bot.message_handler(commands=['OPEN'])
async def open_positions(message):
    """Checks every token from the list for open positions and opens if there isn't any"""
    tokens = message.text.split()[1:]
    try:
        await bybit.process_tokens(message.chat.id, tokens)
    except Exception as e:
        await bot.send_message(message.chat.id, f"{type(e)}: {e}")


@bot.message_handler(commands=['CLOSE'])
async def close_positions(message):
    """Checks every token from the list for open positions and closes them"""
    tokens = message.text.split()[1:]
    tasks = [bybit.close_position(token) for token in tokens]
    result = await asyncio.gather(*tasks)
    logger.info(result)
    result = [str(x) for x in result]
    await bot.send_message(message.chat.id, '\n***\n'.join(result))


@bot.message_handler(commands=['SHOW'])
async def show_positions(message):
    """Shows the information about open positions with USDT settle coin"""
    response = await bybit.show_positions()
    msg = ''
    for pos in response:
        for key in KEYS:
            msg += f'{key}: {pos[key]} | '
        msg += '\n***\n'
    await bot.send_message(message.chat.id,
                           msg if msg else "There are no open positions")


@bot.message_handler(commands=["GETLOG"])
async def get_log(message):
    """Sends the file with application log to the chat"""
    with open("bybit_log.log", "rb") as file:
        f = file.read()
    await bot.send_document(message.chat.id, f,
                            visible_file_name='bybit_log.log')


class Position:
    """Class for controlling the position for a particular symbol."""

    def __init__(self, position):
        if position['size'] not in ['', '0']:
            self.symbol = position['symbol']
            self.side = position['side']
            self.avgPrice = float(position['avgPrice'])
            self.markPrice = float(position['markPrice'])
            self.size = float(position['size'])
            self.positionValue = float(position['positionValue'])
            self.unrealisedPnl = float(position['unrealisedPnl'])
            self.createdTime = int(position['createdTime'])
            self.opened = True
            self.stop_loss_trigger = STOPOUT[0]
            self.pnl_percent = round(
                self.unrealisedPnl / self.positionValue * 100, 2)
            self.limit_opened = False
            self.limit_order_id = None
            self.channel_id = None
        else:
            self.opened = False
            self.size = 0

    def __str__(self):
        return f"{self.symbol}: Pnl = {self.pnl_percent}, Market price = {self.markPrice}, size = {self.size}," \
               f"Stop loss triger = {self.stop_loss_trigger}%, Limit order placed: {self.limit_opened}"

    def update_position(self, position: dict):
        """Uupdates the instance according to the last API response"""
        if position['size'] not in ['', '0']:
            self.markPrice = float(position['markPrice'])
            self.positionValue = float(position['positionValue'])
            self.unrealisedPnl = float(position['unrealisedPnl'])
            self.pnl_percent = round(
                self.unrealisedPnl / self.avgPrice * 100, 2)
        else:
            self.opened = False
            self.size = 0
            logger.info(self.symbol + ": position closed.")

    async def stopout(self):
        """ Cancel the limit order if any and close the position by market price"""
        logger.info(f"{self.symbol}: STOPOUT!")
        if self.limit_opened:
            response1 = await bybit.cancel_order(self.limit_order_id,
                                                 self.symbol)
            await bot.send_message(self.channel_id,
                                   f"{self.symbol}: LIMIT CANCELLED: {response1}")
        response2 = await bybit.close_position(self.symbol)
        await bot.send_message(self.channel_id,
                               f"{self.symbol}: POSITION STOPPED OUT: {response2}")

    async def follow(self):
        """
        Check the position info and update the values until the stop loss is triggered
        :return: None
        """
        while self.opened:
            response = await bybit.get_position(self.symbol)
            self.update_position(response['result']['list'][0])
            if not self.opened:
                logger.info(response)
                await bot.send_message(self.channel_id,
                                       f"{self.symbol}: POSITION CLOSED")
                break
            if self.pnl_percent <= self.stop_loss_trigger:
                await self.stopout()
            elif self.pnl_percent <= TRIGGERS[0]:
                if not self.limit_opened:
                    resp = await bybit.place_order(symbol=self.symbol,
                                                   side="Sell",
                                                   order_type="Limit", qty=1,
                                                   price=self.avgPrice)
                    if resp['retMsg'] == 'OK':
                        logger.info(
                            f"{self.symbol} LIMIT ORDER PLACED: {resp}")
                        await bot.send_message(self.channel_id,
                                               f"{self.symbol} LIMIT ORDER PLACED: {resp}")
                        self.limit_opened = True
                        self.limit_order_id = resp['result']['orderId']

                    else:
                        await bot.send_message(self.channel_id,
                                               f"{self.symbol} LIMIT ORDER ERROR: {resp}")
                        logger.error(resp)
            elif self.stop_loss_trigger < STOPOUT[2] and self.pnl_percent >= \
                    TRIGGERS[2]:
                self.stop_loss_trigger = STOPOUT[2]
                await bot.send_message(self.channel_id,
                                       f"{self.symbol}: Stop loss changed to {STOPOUT[2]}%")
            elif self.stop_loss_trigger < STOPOUT[1] and self.pnl_percent >= \
                    TRIGGERS[1]:
                self.stop_loss_trigger = STOPOUT[1]
                await bot.send_message(self.channel_id,
                                       f"{self.symbol}: Stop loss changed to {STOPOUT[1]}%")
            logger.info(self)
            await asyncio.sleep(2)


class Bybit:
    """Sends requests to ByBit API for opening and closing positions, checks them"""

    def __init__(self, api_key, secret_key, recv_window):
        self.api_key = api_key
        self.secret_key = secret_key
        self.recv_window = recv_window
        self.api_key = api_key
        self.secret_key = secret_key
        self.recv_window = recv_window
        self.base_url = BASE_URL
        self.db = SessionLocal()
        self.opened_positions = []

    async def get_position(self, symbol: str = ""):
        endpoint = "/v5/position/list"
        method = 'GET'
        params = f'category=linear&symbol={symbol}'
        response = await http_request(endpoint, method, params)
        return response

    async def close_position(self, symbol):
        return await self.place_order(symbol, "Sell", "Market", reduce="true")

    async def show_positions(self, settle_coin="USDT"):
        endpoint = "/v5/position/list"
        method = 'GET'
        params = f'category=linear&settleCoin={settle_coin}'
        response = await http_request(endpoint, method, params)
        logger.info(f"SHOW POSITIONS: {response}")
        return response['result']['list']

    async def place_order(self, symbol: str, side: str, order_type: str,
                          qty: float = 0,
                          reduce: str = 'false', price: float = None,
                          category: str = 'linear'):
        endpoint = "/v5/order/create"
        method = "POST"
        params = f'{{"category": "{category}",' \
                 f'"symbol": "{symbol}",' \
                 f'"side": "{side}",' \
                 f'"orderType": "{order_type}",' \
                 f'"qty": "{qty}",' \
                 f'"timeInForce": "GTC",' \
                 f'"reduceOnly": "{reduce}",' \
                 f'"price": "{price}"}}'
        response = await http_request(endpoint, method, params)
        logger.info(f"PLACE ORDER: {params}\n\t\t\t\tRESPONSE: {response}")

        # Add the order to the database
        if response["retMsg"] == "OK":
            order_id = response['result']['orderId']
            create_time = response['time']
            order = Orders(order_id=order_id,
                           symbol=symbol,
                           side=side,
                           order_type=order_type,
                           qty=qty,
                           price=price,
                           create_time=create_time)
            logger.success(order)
            self.db.add(order)
            self.db.commit()
        return response

    async def cancel_order(self, order_id, symbol, category='linear'):
        endpoint = "/v5/order/cancel"
        method = "POST"
        params = f'{{"category": "{category}",' \
                 f'"orderId": "{order_id}",' \
                 f'"symbol": "{symbol}"}}'
        response = await http_request(endpoint, method, params)
        logger.info(f"CANCEL ORDER: {params}\n\t\t\t\tRESPONSE: {response}")
        return response

    async def check_position(self, channel_id: str, symbol: str):
        """Checks if there is a position opened for a particular symbol.
        If there isn't, places the Buy order and starts following the position"""
        response = await self.get_position(symbol)
        if response['retCode'] != 0:
            logger.error(response)
            await bot.send_message(channel_id, f'{symbol} ERROR: {response}')
        else:
            if response['result']['list'][0]['size'] == "0":
                order = await self.place_order(symbol, "Buy", "Market", 1)
                if order['retMsg'] == 'OK':
                    await bot.send_message(channel_id,
                                           f'{symbol}: ORDER PLACED: {order}')
                    await self.follow_position(channel_id, symbol)
                    self.opened_positions.append(symbol)
            else:
                if symbol in self.opened_positions:
                    logger.info(symbol + ": already being followed by bot")
                    await bot.send_message(channel_id,
                                           symbol + ": already being followed by bot")
                else:
                    logger.info(symbol + ": already opened not by bot")
                    await bot.send_message(channel_id,
                                           symbol + ": already opened not by Bybit positions follower bot")
                return symbol + ": already opened not by Bybit positions follower bot"

    async def follow_position(self, channel_id, symbol):
        """Creates the Position object and initiates its following process"""
        response = await self.get_position(symbol)
        position = response['result']['list'][0]
        logger.info("FOLLOWING POSITION: " + str(position))
        await bot.send_message(channel_id, f"{symbol}: FOLLOWING POSITION")
        position = {key: position[key] for key in KEYS}
        position = Position(position)
        position.channel_id = channel_id
        self.opened_positions[symbol] = position
        return await position.follow()

    async def process_tokens(self, channel_id, tokens: List[str] = None,
                             settlle_coin: str = ""):
        """
        Checks and opens positions for every token from the list.
        """
        tasks = [self.check_position(channel_id, token) for token in tokens]
        await asyncio.gather(*tasks)


async def http_request(endpoint: str, method: str = 'GET', payload=''):
    """
    Makes a request of a defined method to the endpoint.
    """
    async with aiohttp.ClientSession() as session:
        time_stamp = str(int(time.time() * 1000))
        signature = gen_signature(API_KEY, SECRET_KEY, payload, time_stamp)
        headers = {
            'X-BAPI-API-KEY': API_KEY,
            'X-BAPI-SIGN': signature,
            'X-BAPI-SIGN-TYPE': '2',
            'X-BAPI-TIMESTAMP': time_stamp,
            'X-BAPI-RECV-WINDOW': RECV_WINDOW,
            'Content-Type': 'application/json'
        }
        if method == "POST":
            async with session.request(method, BASE_URL + endpoint,
                                       headers=headers,
                                       data=payload) as response:
                return await response.json()
        else:
            async with session.request(method,
                                       BASE_URL + endpoint + "?" + payload,
                                       headers=headers) as response:
                return await response.json()


async def main():
    global bybit
    bybit = Bybit(API_KEY, SECRET_KEY, RECV_WINDOW)
    await asyncio.gather(bot.polling())


if __name__ == "__main__":
    asyncio.run(main())
