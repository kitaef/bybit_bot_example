import requests
import time
from database import Positions, SessionLocal
from authorise import gen_signature
from loguru import logger
import sys
from config import API_KEY, SECRET_KEY, RECV_WINDOW, BASE_URL, TG_TOKEN
from typing import List
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot import ExceptionHandler
import aiohttp

log_level = "DEBUG"
# logger.add("file.log", level=log_level, colorize=False, backtrace=True, diagnose=True)

KEYS = {'symbol': str, 'side': str, 'avgPrice': float, 'markPrice': float,
        'size': float, 'positionValue': float, 'unrealisedPnl': float,
        'createdTime': float, 'updatedTime': int}
SL = [-0.2, 0.05, 0.2]  # Test SL values
TRIGGERS = [-0.1, 0.1, 0.2]  # Test triggers
# SL = [-8, 1, 5]  # SL values
# TRIGGERS = [-5, 5, 10]  # Triggers

 # Telegram bot

class BotException(ExceptionHandler):
    def handle(self, exception):
        logger.error(exception)

bot = AsyncTeleBot(TG_TOKEN, exception_handler=BotException())
@bot.message_handler(commands=['start'])
async def start_message(message):
    msg = 'Hello. Meet the ByBit positions follower bot. \n' \
          'It automatically opens "Buy" positions for requested trading instruments at ByBit. ' \
          'Then the bot follows every position according to the next logic:\n' \
          '1) If the Pnl drops to -5%, the bot sends the limit order to sell the position at the price it was opened.\n' \
          '2) If the Pnl drops to -8% (initial stop loss price) and there is already a limit order opened, ' \
          'the bot cancels this order and opens the reduce order to sell the position by market price.\n' \
          '3) If the Pnl rises by 5%, the bot changes the value for stop loss from -8% to open_price + 1%, ' \
          'so that if the price drops back to this level, the position will be closed wth 1% profit\n' \
          '4) If the Pnl rises by 10%, the bot changes the value for stop loss to open_price + 5%\n\n' \
          'The commands supported:\n' \
          '1) /open BTCUSDT ETHUSDT ADAUSDT\n' \
          'Open and start following the positions for the tokens listed\n' \
          '2) /close BTCUSDT ETHUSDT ADAUSDT\n' \
          'Close and stop following the positions for the tokens listed\n' \
          '3) /show\n' \
          'Show the positions followed by the bot (the settle coin is USDT)\n'
    await bot.send_message(message.chat.id, msg)


@bot.message_handler(commands=['open'])
async def open_positions(message):
    """Handles the 'open' command with tokens as arguments"""
    tokens = message.text.split()[1:]
    try:
        await bybit.process_tokens(message.chat.id, tokens)
    except Exception as e:
        await bot.send_message(message.chat.id, f"{type(e)}: {e}")



@bot.message_handler(commands=['close'])
async def close_positions(message):
    tokens = message.text.split()[1:]
    tasks = [bybit.close_position(token) for token in tokens]
    result = await asyncio.gather(*tasks)
    logger.info(result)
    result = [str(x) for x in result]
    await bot.send_message(message.chat.id, '\n***\n'.join(result))


@bot.message_handler(commands=['show'])
async def show_positions(message):
    response = await bybit.show_positions()
    msg = ''
    for pos in response:
        for key in KEYS:
            msg += f'{key}: {pos[key]} | '
        msg += '\n***\n'
    await bot.send_message(message.chat.id, msg if msg else "There are no open positions")


class Position:
    """
    Class for controlling the position for a particular symbol.
    """

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
            self.updatedTime = int(position['updatedTime'])
            self.opened = True
            self.stop_loss_trigger = SL[0]
            self.pnl_percent = round(self.unrealisedPnl / self.positionValue * 100, 2)
            self.limit_opened = False
            self.limit_order_id = None
            self.channel_id = None
        else:
            self.opened = False
            self.size = 0

    def __str__(self):
        return f"{self.symbol}: Pnl = {self.pnl_percent}, Market price = {self.markPrice}, size = {self.size}," \
               f"Stop loss triger = {self.stop_loss_trigger}%, Limit order placed: {self.limit_opened}"

    def update_position(self, position):
        """
        updates the instance according to the last API response
        :param position: dict
        :return: None
        """
        if position['size'] not in ['', '0']:
            self.markPrice = float(position['markPrice'])
            self.positionValue = float(position['positionValue'])
            self.unrealisedPnl = float(position['unrealisedPnl'])
            self.updatedTime = int(position['updatedTime'])
            self.pnl_percent = round(self.unrealisedPnl / self.avgPrice * 100, 2)
        else:
            self.opened = False
            self.size = 0
            logger.info(self.symbol + ": position closed.")


    async def stop_loss(self):
        """
        Cancel the limit order if any and close the position by market price
        :return:
        """
        await bybit.cancel_order(self.limit_order_id, self.symbol)
        await bybit.close_position(self.symbol)
        logger.error(f"{self.symbol}: STOPOUT!")
    async def follow(self):
        """
        Check the position info and update the values until the stop loss is triggered
        :return: None
        """
        while self.opened:
            response = await bybit.get_position(self.symbol)
            self.update_position(response['result']['list'][0])
            if not self.opened:
                await bot.send_message(self.channel_id, f"{self.symbol}: Position closed.")
                return f"{self.symbol}: position closed"
            if self.pnl_percent <= self.stop_loss_trigger:
                await self.stop_loss()
                await bot.send_message(self.channel_id, f"{self.symbol}: Position stopped out.")
            elif self.pnl_percent <= TRIGGERS[0]:
                if not self.limit_opened:
                    resp = await bybit.place_order(symbol=self.symbol, side="Sell", order_type="Limit", qty=1,
                                                   price=self.avgPrice)
                    if resp['retMsg'] == 'OK':
                        self.limit_opened = True
                        self.limit_order_id = resp['result']
                        await bot.send_message(self.channel_id, f"{self.symbol}: Limit order opened")
                    else:
                        logger.error(resp)
            elif self.pnl_percent >= TRIGGERS[2]:
                self.stop_loss_trigger = max(self.stop_loss_trigger, SL[2])
                await bot.send_message(self.channel_id, f"{self.symbol}: Stop loss changed to {SL[2]}%")
            elif self.pnl_percent >= TRIGGERS[1]:
                self.stop_loss_trigger = max(self.stop_loss_trigger, SL[1])
                await bot.send_message(self.channel_id, f"{self.symbol}: Stop loss changed to {SL[1]}%")
            logger.info(self)
            await asyncio.sleep(2)


class Bybit:
    def __init__(self, api_key, secret_key, recv_window):
        self.api_key = api_key
        self.secret_key = secret_key
        self.recv_window = recv_window
        self.api_key = api_key
        self.secret_key = secret_key
        self.recv_window = recv_window
        self.base_url = BASE_URL
        self.db = SessionLocal()
        self.http_session = requests.Session()
        self.opened_positions = {}
        self.closed_positions = {}

    async def get_symbols(self):
        endpoint = "/v5/market/instruments-info"
        method = "GET"
        params = 'category=linear'
        response = await http_request(endpoint, method, params)
        for i in response['result']['list']:
            logger.info(i['symbol'])

    async def get_charts(self):
        endpoint = "/v5/market/mark-price-kline"
        method = "GET"
        params = 'category=linear'
        response = await http_request(endpoint, method, params)
    async def get_orders(self, category: str = 'linear', symbol: str = None, order_id=None):
        endpoint = "/v5/order/realtime"
        method = "GET"
        params = f'category={category}'
        if order_id:
            params += f'&orderId={order_id}'
        elif symbol:
            params += f'&symbol={symbol}'
        else:
            logger.error("order_id or symbol are required")
        response = await http_request(endpoint, method, params)
        logger.info(response)

    async def place_order(self, symbol: str, side: str, order_type: str, qty: float = '0',
                          reduce: str = 'false', price: float = 'null', category: str = 'linear'):
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
        logger.info(f"ORDER PLACED: {params}\n\t\t\t\tRESPONSE: " + str(response))
        return response

    async def cancel_order(self, order_id, symbol, category='linear'):
        endpoint = "/v5/order/cancel"
        method = "POST"
        params = f'{{"category": "{category}",' \
                 f'"orderId": "{order_id}",' \
                 f'"symbol": "{symbol}"}}'
        response = await http_request(endpoint, method, params)
        logger.info(f"CANCEL ORDER: {params}\n\t\t\t\tRESPONSE: " + str(response))
        return response



    async def close_position(self, symbol):
        return await self.place_order(symbol, "Sell", "Market", reduce="true")

    async def follow_position(self, channel_id, symbol):
        response = await self.get_position(symbol)
        position = response['result']['list'][0]
        logger.info("FOLLOWING POSITION: " + str(position))
        await bot.send_message(channel_id, "FOLLOWING POSITION: " + str(position))
        position = {key: position[key] for key in KEYS}
        position = Position(position)
        position.channel_id = channel_id
        self.opened_positions[symbol] = position
        return await position.follow()

    async def show_positions(self, settle_coin="USDT"):
        endpoint = "/v5/position/list"
        method = 'GET'
        params = f'category=linear&settleCoin={settle_coin}'
        response = await http_request(endpoint, method, params)
        logger.info("SHOW POSITIONS: " + str(response['result']['list']))
        return response['result']['list']

    async def get_position(self, symbol: str = ""):
        endpoint = "/v5/position/list"
        method = 'GET'
        params = f'category=linear&symbol={symbol}'
        response = await http_request(endpoint, method, params)
        return response

    async def check_position(self, channel_id: str, symbol: str):
        result = await self.get_position(symbol)
        logger.info(result)
        # if symbol == "BTCUSDT":
        #     await asyncio.sleep(3)  # checking the asynchronous call
        if result['retCode'] != 0:
            logger.error(result)
            await bot.send_message(channel_id, str(result))
        else:
            if result['result']['list'][0]['size'] == "0":
                order = await self.place_order(symbol, "Buy", "Market", 1)
                logger.info(order)
                await bot.send_message(channel_id, str(order))
                return await self.follow_position(channel_id, symbol)
            else:
                logger.info(symbol + ": already opened not by Bybit positions follower bot")
                await bot.send_message(channel_id, symbol + ": already opened not by Bybit positions follower bot")
                return symbol + ": already opened not by Bybit positions follower bot"

    async def process_tokens(self, channel_id, tokens: List[str] = None, settlle_coin: str = ""):
        """
        Checks and opens positions for every token from the list.
        """
        tasks = [self.check_position(channel_id, token) for token in tokens]
        return await asyncio.gather(*tasks)

    async def run_bot(self, tokens):
        await self.process_tokens(tokens)


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
            async with session.request(method, BASE_URL + endpoint, headers=headers, data=payload) as response:
                return await response.json()
        else:
            async with session.request(method, BASE_URL + endpoint + "?" + payload, headers=headers) as response:
                return await response.json()


# get_symbols()

async def main():
    global bybit
    bybit = Bybit(API_KEY, SECRET_KEY, RECV_WINDOW)

    await asyncio.gather(bot.polling())


# asyncio.run(sell(bybit_bot))
asyncio.run(main())
# asyncio.run(bybit_bot.place_order("BTCUSDT", side="Buy", order_type="Limit", qty=1, price = 30000))
# bybit_bot.run_bot(["BTCUSDT", "ADAUSDT", "ETHUSDT"])
