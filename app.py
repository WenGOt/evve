import json
import math
import os
import wave

import moviepy.editor as mp
from flask import Flask, request, render_template, jsonify, send_file, send_from_directory
from pydub import AudioSegment
from werkzeug.utils import secure_filename

import vosk

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/input/'
app.config['OUTPUT_FOLDER'] = 'static/output/'

# Ensure the upload and output folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

def clear_folder(folder_path):
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path):  # Проверяем, что это файл
                os.remove(file_path)  # Удаляем файл
                print(f"Удалён файл: {file_path}")
        except Exception as e:
            print(f"Ошибка при удалении файла {file_path}: {e}")


# Извлечение аудио из видео
def extract_audio_from_video(video_path, audio_output_path):
    video = mp.VideoFileClip(video_path)
    video.audio.write_audiofile(audio_output_path)
    video.audio.close()  # Закрываем аудио для предотвращения утечек
    video.close()  # Закрываем видео для предотвращения утечек

# Конвертация аудио в моно WAV с PCM-кодированием
def convert_to_wav_mono(audio_path, output_path):
    audio = AudioSegment.from_file(audio_path)

    if audio.channels > 1:
        audio = audio.set_channels(1)

    audio = audio.set_frame_rate(16000)
    audio.export(output_path, format="wav")

# Преобразование аудио в текст с помощью Vosk
def speech_to_text(audio_path, model_path):
    wf = wave.open(audio_path, "rb")

    if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
        wf.close()  # Закрытие файла перед выбросом исключения
        raise ValueError("Audio file must be WAV format mono PCM with a 16kHz sample rate.")

    model = vosk.Model(model_path)
    recognizer = vosk.KaldiRecognizer(model, wf.getframerate())
    result_text = ""

    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            result_text += result.get('text', '') + " "

    wf.close()
    return result_text

# Определение количества тем на основе длины видео
def get_num_topics(video_duration):
    if video_duration < 60:
        return 2
    elif video_duration < 3600:
        return min(20, max(2, math.ceil(video_duration / 180)))
    else:
        return 20

# Разделение текста на темы
def split_text_into_topics(text, num_topics):
    sentences = text.split(".")
    if len(sentences) == 0:
        raise ValueError("Текст не содержит предложений.")

    avg_sent_per_topic = max(1, len(sentences) // num_topics)
    topics = []
    for i in range(0, len(sentences), avg_sent_per_topic):
        topic = ". ".join(sentences[i:i + avg_sent_per_topic]).strip()
        if topic:
            topics.append(topic)

    return topics

# Поиск времени ключевых моментов в видео по тексту
def find_key_times(text, video_duration, num_topics):
    total_length = len(text)
    key_times = []

    for i in range(num_topics):
        key_position = (i + 0.5) * total_length // num_topics
        key_time = (key_position / total_length) * video_duration
        key_times.append(key_time)

    return key_times

# Обрезка видео до нужного соотношения сторон
def crop_video_to_aspect_ratio(video, aspect_ratio="16:9"):
    if aspect_ratio == "16:9":
        target_ratio = 16 / 9
    elif aspect_ratio == "4:3":
        target_ratio = 4 / 3
    elif aspect_ratio == "1:1":
        target_ratio = 1 / 1
    elif aspect_ratio == "9:16":
        target_ratio = 9 / 16
    else:
        raise ValueError("Неподдерживаемое соотношение сторон.")

    video_ratio = video.w / video.h

    if video_ratio > target_ratio:
        new_width = video.h * target_ratio
        crop_x = (video.w - new_width) / 2
        return video.crop(x1=crop_x, x2=crop_x + new_width)
    else:
        new_height = video.w / target_ratio
        crop_y = (video.h - new_height) / 2
        return video.crop(y1=crop_y, y2=crop_y + new_height)

# Создание видео для каждой темы с обрезкой до нужного соотношения сторон
def extract_videos_for_topics(video_path, output_prefix, key_times, aspect_ratio="16:9", duration=10):
    video = mp.VideoFileClip(video_path)

    for i, key_time in enumerate(key_times):
        start_time = max(0, key_time - duration // 2)
        end_time = min(video.duration, start_time + duration)

        topic_video = video.subclip(start_time, end_time)
        cropped_video = crop_video_to_aspect_ratio(topic_video, aspect_ratio)

        output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"{output_prefix}_topic_{i + 1}.mp4")

        if aspect_ratio == "9:16":
            cropped_video = cropped_video.resize((1080, 1920))

        cropped_video.write_videofile(output_path, codec="libx264", audio_codec="aac", fps=24)

    video.close()  # Закрываем видео для предотвращения утечек

# Основной процесс
# Основной процесс
def process_video(video_path, model_path, aspect_ratio="16:9", duration=10):
    audio_path = "extracted_audio.wav"
    converted_audio_path = "converted_audio.wav"

    try:
        extract_audio_from_video(video_path, audio_path)
        convert_to_wav_mono(audio_path, converted_audio_path)
        text = speech_to_text(converted_audio_path, model_path)

        video = mp.VideoFileClip(video_path)
        video_duration = video.duration
        num_topics = get_num_topics(video_duration)
        key_times = find_key_times(text, video_duration, num_topics)

        extract_videos_for_topics(video_path, "output_video", key_times, aspect_ratio, duration)
    except Exception as e:
        print(f"Error during video processing: {e}")
        raise

@app.route('/static/output/<path:filename>')
def serve_video(filename):
    return send_file(f'static/output/{filename}', conditional=True)

# Главная страница для загрузки видео
@app.route('/')
def upload_form():
    output_files = os.listdir(app.config['OUTPUT_FOLDER'])
    return render_template('index.html', videos=output_files)

# Обработка загруженного видео
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(file.filename)
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    try:
        clear_folder(app.config['UPLOAD_FOLDER'])
        clear_folder(app.config['OUTPUT_FOLDER'])

        file.save(video_path)

        model_path = "vosk"
        aspect_ratio = request.form.get('aspect_ratio', '16:9')
        duration = int(request.form.get('duration', 10))
        process_video(video_path, model_path, aspect_ratio, duration)

        output_files = os.listdir(app.config['OUTPUT_FOLDER'])
        return jsonify({"output_files": output_files}), 200
    except Exception as e:
        print(f"Ошибка при обработке видео: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/static/output/<path:filename>')
def serve_output(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=True)
