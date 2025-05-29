import json
import requests
import threading
import time
import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech import SpeechConfig, SpeechSynthesizer, AudioConfig
from azure.iot.device import IoTHubDeviceClient, Message, MethodResponse
from azure.cognitiveservices.speech.audio import AudioOutputConfig

# === Configuration ===
speech_api_key = "9Sn9EHNeX1vtzScdJPbRguMVnh6pQnmKKNrFABDeF4qWfuQNaiX9JQQJ99BDACqBBLyXJ3w3AAAYACOGz5lK"
speech_location = "southeastasia"

translator_api_key = "9kEZtG9GBTdkPVYcfO4T9sZyGL1SHrxzxfzMOsBmsQQDgCuaaiUzJQQJ99BEACqBBLyXJ3w3AAAbACOGzndo"
translator_endpoint = "https://api.cognitive.microsofttranslator.com"
translator_region = "southeastasia"

from_language = 'fr-FR'
to_language = 'en-US'

connection_string = "HostName=IOThubdevice.azure-devices.net;DeviceId=LASasuss;SharedAccessKey=D5SUhZKL8uUdZ7YVIwb5w+KH7LC+/fPllTYJnYzPjwY="

get_timer_url = "http://localhost:7071/api/text2timer"

# === Global flag to prevent echo ===
listening = True
listening_lock = threading.Lock()

# === IoT Hub Connection ===
print("Connecting to IoT Hub...")
device_client = IoTHubDeviceClient.create_from_connection_string(connection_string)
device_client.connect()
print("IoT Hub connected.")

# === Receive message from IOT Hub ===
def message_handler(message):
    print("📩 Message received from IoT Hub:", message.data)
    payload = json.loads(message.data)
    text = payload.get("speech", "")
    if text:
        say(text)  # speak the translated message

device_client.on_message_received = message_handler

# === Speech Config ===
speech_config = speechsdk.SpeechConfig(subscription=speech_api_key, region=speech_location)
speech_config.speech_recognition_language = from_language
audio_config = AudioOutputConfig(use_default_speaker=True)

# === Voice Selection (must come before synthesizer init) ===
voice_short_name = None
temp_synth = speechsdk.SpeechSynthesizer(speech_config=speech_config)
voices_result = temp_synth.get_voices_async().get()
voices = voices_result.voices if voices_result else []
voice_short_name = next((v.short_name for v in voices if v.locale.lower() == from_language.lower()), None)

if voice_short_name:
    speech_config.speech_synthesis_voice_name = voice_short_name
    print(f"Using voice: {voice_short_name}")
else:
    print("⚠️ No matching voice found for language.")

# === Final Synthesizer with correct voice ===
synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

# === Translate Function ===
def translate_text(text):
    try:
        url = f"{translator_endpoint}/translate?api-version=3.0"
        headers = {
            'Ocp-Apim-Subscription-Key': translator_api_key,
            'Ocp-Apim-Subscription-Region': translator_region,
            'Content-Type': 'application/json'
        }
        params = {'from': from_language, 'to': to_language}
        body = [{'text': text}]

        print(f"Sending translation request to {url}")
        response = requests.post(url, headers=headers, params=params, json=body, timeout=5)
        response.raise_for_status()

        return response.json()[0]['translations'][0]['text']
    except Exception as e:
        print(f"⚠️ Translation error: {e}")
        return text  # fallback

# === Speech Output ===
def say(text_en):
    if voice_short_name:
        try:
            print(f"🔊 Speaking: {text_en}")
            # 🔇 Stop recognizer during speaking
            recognizer.stop_continuous_recognition_async().get()
            time.sleep(0.3)

            ssml = f"<speak version='1.0' xml:lang='{from_language}'>" \
                   f"<voice xml:lang='{from_language}' name='{voice_short_name}'>{text_en}</voice></speak>"
            synthesizer.speak_ssml_async(ssml).get()

            time.sleep(0.5)  # short delay before restarting
            recognizer.start_continuous_recognition_async().get()
        except Exception as e:
            print(f"⚠️ Speech synthesis error: {e}")
    else:
        print("⚠️ No valid voice to speak.")


# === Timer Functions ===
def announce_timer(minutes, seconds):
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if seconds > 0:
        parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")
    say("Time's up on your " + " ".join(parts) + " timer.")

def create_timer(seconds):
    minutes, secs = divmod(seconds, 60)
    threading.Timer(seconds, announce_timer, args=[minutes, secs]).start()
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if secs > 0:
        parts.append(f"{secs} second{'s' if secs > 1 else ''}")
    say("Timer started for " + " ".join(parts))

# === IoT Hub Method Handler ===
def handle_method_request(request):
    print(f"Received method request: {request.name}")
    if request.name == 'set-timer':
        payload = json.loads(request.payload)
        seconds = payload.get('seconds', 0)
        if seconds > 0:
            create_timer(seconds)
    response = MethodResponse.create_from_method_request(request, 200)
    device_client.send_method_response(response)

device_client.on_method_request_received = handle_method_request

# === Timer Parsing from Local Function with keyword check ===
def get_timer_time(text):
    keywords = ['second', 'seconds', 'minute', 'minutes', 'hour', 'hours']
    if not any(k in text.lower() for k in keywords):
        print("No time keywords found in text, skipping timer parse.")
        return 0
    try:
        response = requests.post(get_timer_url, json={'name': text}, timeout=5)
        response.raise_for_status()
        print("✅ Timer API response:", response.text)
        data = response.json()
        return data.get('seconds', 0)
    except Exception as e:
        print(f"⚠️ Timer parsing error: {e}")
        return 0

# === Processing Recognized Speech ===
def process_text(text):
    with listening_lock:
        if not listening:
            return  # 🚫 Don't process system's own speech

    print(f"🗣 Recognized: {text}")
    fr = translate_text(text)
    device_client.send_message(Message(json.dumps({'speech': fr})))
    say(text)
    seconds = get_timer_time(text)
    if seconds > 0:
        create_timer(seconds)

# === Continuous Recognition Setup ===
recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config)
recognizer.recognized.connect(
    lambda evt: process_text(evt.result.text) if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech else None
)

# === Start Listening ===
recognizer.start_continuous_recognition()
print("🎤 Listening... Speak a command in English.")

# === Main Loop ===
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping...")
    recognizer.stop_continuous_recognition()
    device_client.disconnect()
    print("Exited cleanly.")
