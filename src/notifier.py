import requests

# BotFather에서 받은 토큰
TOKEN = "8875903407:AAGAlqsJ8NBNqSo4RNExxiawzIOKrZNtqJs"

# 채널 아이디 (chat.id)
CHANNEL_ID = '8933751011'

# 보낼 메시지
MESSAGE = '안녕하세요! 텔레그램 봇에서 보낸 메시지입니다 🚀'

url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
payload = {
    'chat_id': CHANNEL_ID,
    'text': MESSAGE
}

response = requests.post(url, data=payload)
print(response.json())