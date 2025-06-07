import json
import threading
import time
import requests
import datetime
import azure.cognitiveservices.speech as speechsdk
from azure.cognitiveservices.speech import SpeechConfig, SpeechSynthesizer, AudioConfig
from azure.iot.device import IoTHubDeviceClient, Message, MethodResponse
from azure.cognitiveservices.speech.audio import AudioOutputConfig

# === Configuration ===
speech_api_key = "9Sn9EHNeX1vtzScdJPbRguMVnh6pQnmKKNrFABDeF4qWfuQNaiX9JQQJ99BDACqBBLyXJ3w3AAAYACOGz5lK"
speech_location = "southeastasia"

from_language = 'fr-FR'  # tiếng Pháp

device_id = "LASasuss"
connection_string = "HostName=smarttimerhub.azure-devices.net;DeviceId=LASasuss;SharedAccessKey=6xUCjprfwYjA1nBZKlCKoLqytFGpF6vv2K1jtWH6w9Y="

get_timer_url = "http://localhost:7071/api/text2timer"

# === Global flag to prevent echo ===
speaking_lock = threading.Lock()
is_speaking = False

# === IoT Hub Connection ===
try:
    print("Connecting to IoT Hub...")
    device_client = IoTHubDeviceClient.create_from_connection_string(connection_string)
    device_client.connect()
    print("IoT Hub connected.")
except Exception as e:
    print(f"Failed to connect to IoT Hub: {e}")
    exit(1)

# === Speech Config ===
speech_config = speechsdk.SpeechConfig(subscription=speech_api_key, region=speech_location)
speech_config.speech_recognition_language = from_language
audio_config = AudioOutputConfig(use_default_speaker=True)

# Get available voices
temp_synth = speechsdk.SpeechSynthesizer(speech_config=speech_config)
voices_result = temp_synth.get_voices_async().get()
voices = voices_result.voices if voices_result else []
voice_short_name = next((v.short_name for v in voices if v.locale.lower() == from_language.lower()), None)

if not voice_short_name:
    # Fallback voice if no exact match found
    voice_short_name = "fr-FR-HenriNeural"
    print(f"No matching voice found for {from_language}. Using fallback voice: {voice_short_name}")
else:
    print(f"Using voice: {voice_short_name}")

# Final Synthesizer
synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

# === Timer Functions ===
def announce_timer(minutes, seconds):
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if seconds > 0:
        parts.append(f"{seconds} second{'s' if seconds > 1 else ''}")
    say("Le temps est écoulé pour votre minuterie de " + " ".join(parts) + ".")

def create_timer(seconds):
    minutes, secs = divmod(seconds, 60)
    threading.Timer(seconds, announce_timer, args=[minutes, secs]).start()
    parts = []
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if secs > 0:
        parts.append(f"{secs} seconde{'s' if secs > 1 else ''}")
    say("Minuterie démarrée pour " + " ".join(parts) + ".")

# === Say Function ===
def say(text_to_speak):
    global is_speaking
    with speaking_lock:
        is_speaking = True
    print(f"{datetime.datetime.now()} Speaking: {text_to_speak}")
    try:
        ssml = f"<speak version='1.0' xml:lang='{from_language}'>" \
               f"<voice xml:lang='{from_language}' name='{voice_short_name}'>{text_to_speak}</voice></speak>"
        synthesizer.speak_ssml_async(ssml).get()
    except Exception as e:
        print(f"Speech synthesis error: {e}")
    with speaking_lock:
        is_speaking = False

# === Timer Parsing from Local Function ===
def get_timer_time(text):
    keywords = ['second', 'seconds', 'minute', 'minutes', 'heure', 'heures']
    if not any(k in text.lower() for k in keywords):
        print(f"{datetime.datetime.now()} ⏭ No time keywords found in text, skipping timer parse.")
        return 0
    try:
        response = requests.post(get_timer_url, json={'name': text}, timeout=5)
        response.raise_for_status()
        print(f"{datetime.datetime.now()} Timer API response: {response.text}")
        data = response.json()
        return data.get('seconds', 0)
    except Exception as e:
        print(f"Timer parsing error: {e}")
        return 0

# === Message Handler ===
def message_handler(message):
    global is_speaking
    try:
        payload = json.loads(message.data)
        text = payload.get("speech", "")
        if text:
            while True:
                with speaking_lock:
                    if not is_speaking:
                        break
                time.sleep(0.1)
            print(f"{datetime.datetime.now()} Message received: {text}")
            say(text)

            seconds = get_timer_time(text)
            if seconds > 0:
                create_timer(seconds)
    except Exception as e:
        print(f"Error in message_handler: {e}")

device_client.on_message_received = message_handler

# === IoT Hub Method Handler ===
def handle_method_request(request):
    print(f"{datetime.datetime.now()} Received method request: {request.name}")
    if request.name == 'set-timer':
        try:
            payload = json.loads(request.payload)
            seconds = payload.get('seconds', 0)
            if seconds > 0:
                create_timer(seconds)
        except Exception as e:
            print(f"Error handling method request payload: {e}")
    response = MethodResponse.create_from_method_request(request, 200)
    device_client.send_method_response(response)

device_client.on_method_request_received = handle_method_request

print("Device B running. Waiting for messages...")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nStopping Device B...")
    device_client.disconnect()
    print("Exited cleanly.")
