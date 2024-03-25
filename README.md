# bybit_bot_example

This is an example of a Telegram bot that can open and follow your trading positions on the ByBit.

In this project I practised using:
- The ByBit API
- Asyncio and aiohttp for threading
- Telebot for handling messages and commands
- SQL Alchemy and PostgreSQL for storing the order history.

The bot follows the below logic:<br>
1. When it receives a command with a list of trading instruments it starts a separate thread for each of them. If there isn't an open position for the symbol, the bot places the order for 1 lot in BUY direction.<br>
2. If the PnL drops by 5%, the bot places the limit order to sell this position by the actual price.<br>
3. If there is a limit order already opened and the PnL drops by 8%, it triggers the Stop Loss (cancelling the limit order and closing the position by market price)
4. If the price goes up by 5% the stop loss is set at the level of the open price + 1%. If the price goes up by 10% the stop loss is set at the open price + 5%
5. The positions should be followed until is is closed by the limit order or the Stop Loss.

#### Run instructions
1. Fill in the credentials in config.py
- Buybit API and Secret keys
- Telegram bot token
- Postgres credentials
2. Run the main.py
