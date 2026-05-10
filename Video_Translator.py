import numpy as np
import pickle
import os
import sys
import re
import argparse
import shutil
from pathlib import Path
#import pysrt
from num2words import num2words
from tqdm import tqdm
from fsorter import fsorter
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips

BASE_DIR = Path(__file__).resolve().parent
VOCAL_REMOVER_DIR = BASE_DIR / "vocal-remover"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
PYTHON = sys.executable
MOVIEPY_DURATION_EPSILON = 0.05
MIN_CLIP_DURATION = 0.04


def run_command(args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def ensure_ffmpeg():
    if not FFMPEG or not Path(FFMPEG).exists():
        raise RuntimeError(
            "ffmpeg bulunamadi. Homebrew ile `brew install ffmpeg` kurup tekrar deneyin."
        )
    return FFMPEG


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_media_end(clip, requested_end=None, start=0.0):
    durations = []
    if requested_end is not None:
        durations.append(safe_float(requested_end))
    if getattr(clip, "duration", None) is not None:
        durations.append(safe_float(clip.duration))
    if getattr(clip, "audio", None) is not None and getattr(clip.audio, "duration", None) is not None:
        durations.append(safe_float(clip.audio.duration))
    if not durations:
        return None

    end = min(durations)
    if end - start > MOVIEPY_DURATION_EPSILON:
        end -= MOVIEPY_DURATION_EPSILON
    return end if end - start >= MIN_CLIP_DURATION else None

def mp4_to_wav(video_path):
    video_path = Path(video_path).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video dosyasi bulunamadi: {video_path}")

    output_dir = BASE_DIR / "original_wav"
    output_dir.mkdir(exist_ok=True)
    video_wav_name = video_path.stem
    wav_path = output_dir / f"{video_wav_name}.wav"
    run_command([ensure_ffmpeg(), "-y", "-i", video_path, wav_path])

    return str(wav_path), str(video_path)


def seperate_Speaker_and_Background_Sound_from_audio(audio_path, file_path): #  1
    """
    STEP 1 :
        Seperate Speaker and Audio from original audio (wav, mp3, etc.)

    """
    
    model_path = VOCAL_REMOVER_DIR / "models" / "baseline.pth"
    if not model_path.exists():
        raise FileNotFoundError(
            f"Vocal remover modeli eksik: {model_path}\n"
            "Indirmek icin: curl -L https://huggingface.co/fabiogra/baseline_vocal_remover/resolve/main/baseline.pth "
            "-o vocal-remover/models/baseline.pth"
        )
    run_command(
        [PYTHON, "inference.py", "--input", audio_path, "--output_dir", file_path],
        cwd=VOCAL_REMOVER_DIR,
    )
    return True


def transcript_Text(vocal_audio_path, source_language): # 
    """
    STEP 2 :
        Extract transcript from seperated audio(return --> seperate_Speaker)
        
    """
    
    import torch
    import whisper
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    batch_size = 8 # reduce if low on GPU mem
    compute_type = "int8" # change to "int8" if low on GPU mem (may reduce accuracy)
    # transcribe with original whisper
    model = whisper.load_model("base", device=device) # 1. parameter --> (tiny, base, small, medium, large) and 2. parameter is device
    audio = whisperx.load_audio(vocal_audio_path)
    
    if source_language != None:
        result = model.transcribe(audio, language=source_language, verbose=True) # batch_size=batch_size
    else:
        audio_for_detection = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio_for_detection).to(model.device)
        _, probs = model.detect_language(mel)
        auto_lang = max(probs, key=probs.get)
        print(f"Detected language: {max(probs, key=probs.get)}")
        result = model.transcribe(audio, language=auto_lang, verbose=True)
        
    print(result["segments"]) # before alignment
    
    
    
    #                   BURAYA BAK !!!!
# =============================================================================
#     
#     for seg in range(len(result.get('segments'))):
#         org_text = result.get('segments')[seg].get('text')
#         org_text = org_text.split(' ')
#         final_sentence = list()
#         for index in range(len(org_text)):
#             if len(org_text[index]) > 0 :
#                 splitted_text = re.split(r'[^\w\s]', org_text[index])
#                 cleaned_split_text = [word for word in splitted_text if word.strip() != '']
#                 if len(cleaned_split_text) > 0 and cleaned_split_text[0].isdigit():
#                     convert_num_2_text = num2words(cleaned_split_text, lang=source_language)
#                     if len(org_text[index]) > cleaned_split_text:
#                         convert_num_2_text += org_text[index][-1] 
#                     final_sentence.append(convert_num_2_text)
#                 
#                 
# =============================================================================
                
                
                
            #final_sentence.append(cleaned_split_text)
        
        # set1 = set(list(final_sentence[0][0]))
        # set2 = set(list(org_text[0]))
                
    
    # load alignment model and metadata
    model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
    # align whisper output
    result_aligned = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=True) # return_char_alignments --> Character based TimeStamps!
    transcript = result_aligned["word_segments"]
    
    removable_index = list()
    for i in range(len(transcript)):
        if transcript[i].get('start') == None: # and result.get('segments')[i].get('start') != None
            removable_index.append(i)
    transcript_edit = transcript
    i = 0
    for i in range(len(removable_index)):        
        transcript_edit.pop(removable_index[i])
        if i == (len(removable_index) - 1): 
            break
        removable_index[i+1] -= 1

        # for i in range(len(removable_index)):
        #     if i > 0:
        #         i -= 1
        #     transcript.pop(removable_index[i])
            
    else:
        pass
            
            # text = transcript[i].get('text')
            # start = result.get('segments')[i].get('start')
            # end = result.get('segments')[i].get('end')
            # new_dict = {'word': text, 'start': start, 'end': end}
            # transcript[i].update(new_dict)
            # del transcript[i]['text']

    return transcript


def save_Transcript(vocal_audio_path, transcript_path, source_language): #  2
    transcript = transcript_Text(vocal_audio_path, source_language)
    with open(transcript_path, "wb") as fp:   #Pickling
      pickle.dump(transcript, fp)
    

def read_Transcript(transcript_path):  #  3
    with open(transcript_path, "rb") as fp:   # Unpickling
        loaded_transcript = pickle.load(fp)
        
    return loaded_transcript


def create_TimeStamps(loaded_transcript, character_set): #  5 
    start = list()
    end = list()
    for i in tqdm(range(len(loaded_transcript))):
        if i == 0 and len(loaded_transcript) == 1:
            start.append(loaded_transcript[i].get('start'))
            end.append(loaded_transcript[i].get('end') ) # kelime sonları çok hızlı bittiği için 0.5 eklendi.!
        elif i == 0:
            start.append(loaded_transcript[i].get('start')) # round(loaded_transcript[i].get('start'), 4)
        elif i == len(loaded_transcript) - 1:
            end.append(loaded_transcript[i].get('end') )
        
        elif loaded_transcript[i].get('word')[-1] in character_set:
                end.append(loaded_transcript[i].get('end') )
                start.append(loaded_transcript[i+1].get('start')) # round(loaded_transcript[i+1].get('start'), 4)
    
    return start, end


def create_Sentences(loaded_transcript, character_set): #  6
    sentences = list()
    text = str()
    for i in tqdm(range(len(loaded_transcript))):
        text +=  loaded_transcript[i].get('word')
        if (text[-1] in character_set) != True:
            text += ' '
        else:
            sentences.append((text))
            text = ''
    if text.strip():
        sentences.append(text.strip())
    
    return sentences


def gender_Dedection(start, end, vocal_audio_path): #  7
    if ('gender_dedector' in os.listdir()) != True:    
        os.mkdir('gender_dedector')
    gender_dedection_path = os.getcwd() + '/gender_dedector/'
    vocal_audioclip = AudioFileClip(vocal_audio_path)
    gender = list()
    for i in range(len(start)):
        #gender_clip = videoclip.subclip(start[i], end[i])
        gender_clip = vocal_audioclip.subclip(start[i], end[i])
        gender_clip.write_audiofile(gender_dedection_path + f'{i+1}.wav')
        #gender_audio = gender_clip.audio
        #gender_audio.write_audiofile(f'/Users/kadirbarut/Desktop/veris/output/gender_dedector/{i+1}.wav')
        if ('gender-recognition-by-voice' in os.listdir()) != True:
            subprocess.call('git clone https://github.com/x4nth055/gender-recognition-by-voice', shell=True)
        os.chdir('gender-recognition-by-voice')
        voices = subprocess.Popen('python test.py --file ' + gender_dedection_path + f'{i+1}.wav', stdout=subprocess.PIPE, stderr=None, shell=True)
        os.chdir('..')
        #out, err = voices.communicate()
        text = voices.communicate()[0].decode("utf-8")
        gender_split = text.split('\n')
        for i in range(1, len(gender_split)+1):
            if len(gender_split[-i]) == 0:
                gender_split.pop(-i)
            else:
                gender_type = gender_split[-i]
                gender_type = gender_type.split(':')
                gender_type[1] = gender_type[1].split()
                gender.append(gender_type[1])
                break
    
    return gender, gender_dedection_path


def create_Speaker_Reference_Clips(start, end, vocal_audio_path):
    speaker_reference_dir = BASE_DIR / "speaker_reference_clips"
    speaker_reference_dir.mkdir(exist_ok=True)
    vocal_audioclip = AudioFileClip(vocal_audio_path)
    for i in range(len(start)):
        reference_clip = vocal_audioclip.subclip(start[i], end[i])
        reference_clip.write_audiofile(str(speaker_reference_dir / f"{i+1}.wav"))
    vocal_audioclip.close()
    return str(speaker_reference_dir) + "/"
    
    
def macbook_Speaker(sentences, path_wav, source_language, target_language, speaker_wav_dir): #  8 gender
    import torch
    from deep_translator import GoogleTranslator
    from TTS.api import TTS

    translator_source = source_language if source_language is not None else "auto"
    translator = GoogleTranslator(source=translator_source, target=target_language)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    for i in tqdm(range(len(sentences))):
        text = translator.translate(sentences[i])
        # if gender[i][0] == 'male':
        #     subprocess.call(f'say -v Cem "{text}" -o {path_wav}{i+1}.aiff', shell=True)
        #     aiff_file = f"{path_wav}{i+1}.aiff"
        #     wav_file = f"{path_wav}{i+1}.wav"  # +1
        #     data, samplerate = sf.read(aiff_file)
        #     sf.write(wav_file, data, samplerate)
        #     os.remove(f"{path_wav}{i+1}.aiff")
        # elif gender[i][0] == 'female':
        #     subprocess.call(f'say -v Yelda "{text}" -o {path_wav}{i+1}.aiff', shell=True)
        #     aiff_file = f"{path_wav}{i+1}.aiff"
        #     wav_file = f"{path_wav}{i+1}.wav"  # +1
        #     data, samplerate = sf.read(aiff_file)
        #     sf.write(wav_file, data, samplerate)
        #     os.remove(f"{path_wav}{i+1}.aiff")
        
        
# =============================================================================
# ##############################################################################
#         if i == 0:
#             # Get device
#             device = "cuda" if torch.cuda.is_available() else "cpu"
#             tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
# 
#         wav_file = f"{path_wav}{i+1}.wav"
#         if gender[i][0] == 'male':
#             tts.tts_to_file(text=text, speaker_wav='/Users/kadirbarut/Desktop/kevin.wav', language=target_language, file_path=wav_file)
#         elif gender[i][0] == 'female':
#             tts.tts_to_file(text=text, speaker_wav='/Users/kadirbarut/Desktop/woman_wth_kevin.wav', language=target_language, file_path=wav_file)
#         
# ###############################################################################
# =============================================================================


        wav_file = f"{path_wav}{i+1}.wav"
        speaker_wav = Path(speaker_wav_dir) / f"{i+1}.wav"
        if not speaker_wav.exists():
            raise FileNotFoundError(f"Konusmaci referans sesi bulunamadi: {speaker_wav}")
        tts.tts_to_file(text=text, speaker_wav=str(speaker_wav), language=target_language, file_path=wav_file)



        
# =============================================================================
#         subprocess.call(f'say -v Yelda "{text}" -o {path_wav}{i+1}.aiff', shell=True)
#         aiff_file = f"{path_wav}{i+1}.aiff"
#         wav_file = f"{path_wav}{i+1}.wav"  # +1
#         data, samplerate = sf.read(aiff_file)
#         sf.write(wav_file, data, samplerate)
#         os.remove(f"{path_wav}{i+1}.aiff")
# =============================================================================


def wav_to_mp3(sentences, path_wav, path_mp3): #  9
    sort_wav = fsorter.fileSort(path_wav, ['.wav'])
    just_name = list()
    for i in tqdm(range(len(sentences))):  
        audio = AudioFileClip(path_wav+f'{i+1}.wav')
        file = sort_wav[i].split('.')
        just_name.append(file[0])
        audio.write_audiofile(path_mp3+f'{i+1}.mp3')


def save_Subclips(sentences, start, end, videoclip, path_mp4, video_path_concat): #  10
    for i in tqdm(range(len(sentences))):
        #tts = gTTS(translator.translate(sentences[i]), lang='de', slow=False) # Türkçe değilse bunu kullan!
        #clip = videoclip.subclip(start[i], end[i]) # 2. --> 2.54
        run_command([
            ensure_ffmpeg(), "-y", "-i", video_path_concat,
            "-ss", start[i], "-to", end[i],
            "-c:v", "libx264", "-c:a", "aac", f"{path_mp4}{i+1}.mp4",
        ])

        #clip.write_videofile(path_mp4+f'{i+1}.mp4', codec="libx264", audio_codec="aac")
        #list_extend.append(clip)
        #tts.save(f'/Users/kadirbarut/Desktop/veris/output/mp3/{i+1}.mp3') # Türkçe değilse bunu kullan!
        

def change_ClipSpeed(start, path_ffmpeg, ffmpeg_optimized, path_mp3, path_mp4, path_wav, final_movie): #  11
    sort_wav = fsorter.fileSort(path_wav, ['.wav'])    
    #sort_mp3 = fsorter.fileSort(path_mp3, ['.mp3'])
    sort_mp4 = fsorter.fileSort(path_mp4, ['.mp4'])
    for i in tqdm(range(len(start))):
        videoclip = VideoFileClip(path_mp4 + sort_mp4[i]) 
        #audioclip = AudioFileClip(path_mp3 + sort_mp3[i])
        audioclip = AudioFileClip(path_wav + sort_wav[i])
        adjusted_audio_path = path_wav + sort_wav[i]
        if audioclip.duration != videoclip.duration:
            result = audioclip.duration / videoclip.duration # np.float16(audioclip.duration / videoclip.duration)
            if result < 0.5:
                result = 0.5
            #result = round(audioclip.duration / videoclip.duration, 5)
            #subprocess.call('ffmpeg -y -i ' +(path_mp3 + sort_mp3[i]) + ' -filter:a "atempo="'+ str(result)+' '+ (path_mp3 + sort_mp3[i]), shell=True)
            adjusted_audio_path = path_ffmpeg + sort_wav[i]
            run_command([ensure_ffmpeg(), "-y", "-i", (path_wav + sort_wav[i]), "-filter:a", f"atempo={result}", adjusted_audio_path])
            audioclip = AudioFileClip(adjusted_audio_path)
            if videoclip.duration != audioclip.duration:
                result = audioclip.duration / videoclip.duration
                if result < 0.5:
                    result = 0.5
                    #os.remove(path_ffmpeg + sort_mp3[i])
                    adjusted_audio_path = ffmpeg_optimized + sort_wav[i]
                    run_command([ensure_ffmpeg(), "-y", "-i", (path_ffmpeg + sort_wav[i]), "-filter:a", f"atempo={result}", adjusted_audio_path])
                    audioclip = AudioFileClip(adjusted_audio_path)
            else:
                audioclip = AudioFileClip(path_ffmpeg + sort_wav[i])
            #videoclip = videoclip.set_audio(audioclip)
# =============================================================================
#             videoclip = videoclip.set_audio(CompositeAudioClip([audioclip.set_start(0)]))
#             videoclip.write_videofile(final_movie+f'{i+1}.mp4', codec="libx264", audio_codec="aac")  #fps=fps, codec=codec, preset=preset, bitrate=bitrate (Hatalı çalışıyor!! milisaniye ekliyor video sonuna)        
# =============================================================================
            silent_video = f"{path_mp4}{i}_sessiz.mp4"
            run_command([ensure_ffmpeg(), "-y", "-i", f"{path_mp4}{sort_mp4[i]}", "-c:v", "copy", "-an", silent_video])
            run_command([ensure_ffmpeg(), "-y", "-i", silent_video, "-i", adjusted_audio_path, "-c:v", "copy", "-c:a", "aac", f"{final_movie}{sort_mp4[i]}"])
            os.remove(path_mp4+str(i)+'_sessiz.mp4')
        else:
# =============================================================================
#             videoclip = videoclip.set_audio(CompositeAudioClip([audioclip.set_start(0)]))
#             videoclip.write_videofile(final_movie+f'{i+1}.mp4', codec="libx264", audio_codec="aac") # fps=fps, codec=codec, preset=preset, bitrate=bitrate
# =============================================================================   
            silent_video = f"{path_mp4}{i}_sessiz.mp4"
            run_command([ensure_ffmpeg(), "-y", "-i", f"{path_mp4}{sort_mp4[i]}", "-c:v", "copy", "-an", silent_video])
            run_command([ensure_ffmpeg(), "-y", "-i", silent_video, "-i", adjusted_audio_path, "-c:v", "copy", "-c:a", "aac", f"{final_movie}{sort_mp4[i]}"])
            os.remove(path_mp4+str(i)+'_sessiz.mp4')
            
def clip_Parts_without_speaker(start, end, videoclip): #  12
    videoclip = videoclip.without_audio()
    video_bosluklari = list()

    def make_gap(gap_start, gap_end):
        gap_start = max(0.0, safe_float(gap_start))
        gap_end = min(safe_float(gap_end), safe_float(videoclip.duration))
        if gap_end - gap_start >= MIN_CLIP_DURATION:
            return videoclip.subclip(gap_start, gap_end)
        return None

    if not start:
        return [videoclip]

    video_bosluklari.append(make_gap(0.0, start[0]))
    for i in tqdm(range(1, len(start))):
        video_bosluklari.append(make_gap(end[i-1], start[i]))
    video_bosluklari.append(make_gap(end[-1], videoclip.duration))
    
    return video_bosluklari


def timestamps_Without_Speaker_Parts(start, path_mp4, sort_mp4): #  13
    videoclip_durations = list()
    for i in range(len(start)):
        videoclip = VideoFileClip(path_mp4 + sort_mp4[i])
        videoclip_durations.append(videoclip.duration) 
        videoclip.close()
        
    return videoclip_durations 
        

def remove_and_setting_video_ends(start, video_bosluklari, final_movie, path_mp4): #  14
    sort_mp4 = fsorter.fileSort(path_mp4, ['.mp4'])
    videoclip_durations = timestamps_Without_Speaker_Parts(start, path_mp4, sort_mp4)
    list_extend = list()
    for i in range(len(start)):
        #clip = videoclip.subclip(start[i], end[i]) # VideoFileClip(f'/Users/kadirbarut/Desktop/veris/output/final_movie/{i+1}.mp4') # final_movie
        clip = VideoFileClip(final_movie + sort_mp4[i])
        clip_end = safe_media_end(clip, requested_end=videoclip_durations[i])
        if clip_end is None:
            clip.close()
            print(f"Warning: {final_movie + sort_mp4[i]} cok kisa oldugu icin atlandi.")
            continue
        list_extend.append(clip.subclip(0.0, clip_end))
        
    combined_clips = []
    for i in tqdm(range(len(start))):
        if i < len(video_bosluklari) and video_bosluklari[i] is not None:
            combined_clips.append(video_bosluklari[i])
        if i < len(list_extend):
            combined_clips.append(list_extend[i])
    if len(video_bosluklari) > len(start) and video_bosluklari[-1] is not None:
        combined_clips.append(video_bosluklari[-1])

    video_bosluklari[:] = combined_clips


def concatenate_All_Clip_Parts(video_bosluklari): #  15
    # for i in video_bosluklari:
    #     i.write_videofile(f'/Users/kadirbarut/Desktop/total_clips/{i}.mp4', audio_codec="aac")
    # for i in range(len(video_bosluklari)):
    #     video_bosluklari[i].write_videofile(f'/Users/kadirbarut/test_node/F-Augmenter_last/vocal-remover/final_video/test/{i+1}.mp4')
    final = concatenate_videoclips(video_bosluklari)
    # final = subprocess.call('ffmpeg -f concat -safe 0 -i /Users/kadirbarut/Desktop/mylist.txt -c copy output.mp4', shell=True)
    return final


def save_Final_Video(final, final_video_name): #  16
    final_video_dir = Path(os.getcwd()) / "final_video"
    final_video_full_dir = final_video_dir / "final"
    final_video_dir.mkdir(exist_ok=True)
    final_video_full_dir.mkdir(exist_ok=True)
    final_video_path = str(final_video_dir / final_video_name)
    final__full_video_path = str(final_video_full_dir / final_video_name)
    final.write_videofile(f'{final_video_path}', codec="libx264", audio_codec="aac") # fps=fps, codec=codec, preset=preset, bitrate=bitrate
    
    return final_video_path, final__full_video_path


def volume_Settings_Background_Sound(instrumental_audio_path, ): #  17
    # Arka Plan Sesi Volume Ayarı
    run_command([ensure_ffmpeg(), "-i", instrumental_audio_path, "-filter:a", "volume=2.0", str(BASE_DIR / "background_volume_adjusted.wav")])



def concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path): #  18
    run_command([
        ensure_ffmpeg(), "-y", "-i", final_video_path, "-i", instrumental_audio_path,
        "-c:v", "copy",
        "-filter_complex", "[0:a]aformat=fltp:44100:stereo,apad[0a];[1]aformat=fltp:44100:stereo,volume=1[1a];[0a][1a]amerge[a]",
        "-map", "0:v", "-map", "[a]", "-ac", "2", final__full_video_path,
    ])

LANGUAGES = {
    'Auto Detect': 'automatic',
    'English': 'en',
    'Italian': 'it',
    'Spanish': 'es',
    'French': 'fr',
    'German': 'de',
    'Portuguese': 'pt',
    'Japanese': 'ja',
    'Russian': 'ru',
    'Turkish': 'tr',
    'Dutch': 'nl',
    'Polish': 'pl',
    'Czech': 'cs',
}


def normalize_language(value, allow_auto=False):
    if value is None:
        return None
    value = value.strip()
    if allow_auto and value.lower() in {"auto", "automatic", "auto detect"}:
        return None
    for name, code in LANGUAGES.items():
        if value.lower() == name.lower() or value.lower() == code.lower():
            return None if code == "automatic" else code
    valid = ", ".join(f"{name}={code}" for name, code in LANGUAGES.items())
    raise ValueError(f"Gecersiz dil: {value}. Gecerli degerler: {valid}")


def ask_language(prompt, allow_auto=False):
    languages = LANGUAGES if allow_auto else {k: v for k, v in LANGUAGES.items() if v != "automatic"}
    [print(element, ' ----> ', number + 1) for number, element in enumerate(list(languages.keys()))]
    selected_index = int(input(prompt))
    selected_code = list(languages.values())[selected_index - 1]
    return None if selected_code == "automatic" else selected_code


def parse_args():
    parser = argparse.ArgumentParser(description="Video dublaj/ceviri pipeline'i")
    parser.add_argument("video", nargs="?", help="Cevirilecek video dosyasinin yolu")
    parser.add_argument("--source-language", "-s", help="Kaynak dil kodu veya adi. Ornek: tr, en, auto")
    parser.add_argument("--target-language", "-t", help="Hedef dil kodu veya adi. Ornek: en, tr")
    parser.add_argument(
        "--speaker-wav-dir",
        help="XTTS icin 1.wav, 2.wav seklinde konusmaci referans seslerinin bulundugu klasor. Verilmezse vokal parcalarindan uretilir.",
    )
    parser.add_argument("--list-languages", action="store_true", help="Desteklenen dil kodlarini yazdir")
    return parser.parse_args()


def main():
    
    # 1 ---------------------------------------
    
    args = parse_args()
    if args.list_languages:
        for name, code in LANGUAGES.items():
            print(f"{name}: {code}")
        return

    print('\n\n'+' '*30 + '* VIDEO TRANSLATOR *\n\n')

    video_path = args.video or input("\nVideo dosyasi yolu: ").strip()
    source_language = normalize_language(args.source_language, allow_auto=True) if args.source_language else ask_language('\nPlease select the original video language number : ', allow_auto=True)
    target_language = normalize_language(args.target_language) if args.target_language else ask_language('\nPlease select the target video language number : ')
    
    audio_path, video_path_concat = mp4_to_wav(video_path)
    videoclip = VideoFileClip(video_path_concat)
    os.chdir(VOCAL_REMOVER_DIR)
    if ('vocals' in os.listdir()) != True:
        os.mkdir('vocals')
    file_path = 'vocals/'
    
    try:
        if (os.path.basename(audio_path).split('.')[0] + '_Vocals.wav' in os.listdir('vocals/')) != True:
            seperate_Speaker_and_Background_Sound_from_audio(audio_path, file_path)
        os.chdir(BASE_DIR)
    except Exception as exc:
        os.chdir(BASE_DIR)
        raise RuntimeError('Vocal separating is not working!') from exc
     
    # 2 ----------------------------------------
    
    if ('transcripts' in os.listdir()) != True:
        os.mkdir('transcripts')
    transcript_path = os.path.splitext(audio_path)[0].split('/')[-1]
    transcript_path = str(os.getcwd() + '/transcripts/' + transcript_path)
    
    os.chdir(VOCAL_REMOVER_DIR)
    vocal_audio_path = os.getcwd() + "/" + file_path + os.path.splitext(audio_path)[0].split('/')[-1] +'_Vocals.wav'
    instrumental_audio_path = os.getcwd() + "/" + file_path + os.path.splitext(audio_path)[0].split('/')[-1] +'_Instruments.wav'
    
    split_transcript_name = transcript_path.split('/')[-1]
    split_transcript_folder = transcript_path.split('/')
    split_transcript_folder.pop(-1)
    split_transcript_folder = '/'.join(split_transcript_folder) + str('/')
    if (split_transcript_name in os.listdir(split_transcript_folder)) == False:
        save_Transcript(vocal_audio_path, transcript_path, source_language)
    
    # 3 -----------------------------------------
    
    loaded_transcript = read_Transcript(transcript_path)
    
    # 4 -----------------------------------------
    character_set = ['.', '!', '?']
    start, end = create_TimeStamps(loaded_transcript, character_set)
    
    # 5 -----------------------------------------
    
    sentences = create_Sentences(loaded_transcript, character_set)
    
    # 6 -----------------------------------------
    
    speaker_wav_dir = args.speaker_wav_dir
    if speaker_wav_dir is None:
        speaker_wav_dir = create_Speaker_Reference_Clips(start, end, vocal_audio_path)
    else:
        speaker_wav_dir = str(Path(speaker_wav_dir).expanduser().resolve()) + "/"
    # 7 -----------------------------------------
    
    dir_list = ['wav','mp3','mp4','final_movie','ffmpeg','ffmpeg_optimized']
    for i in range(len(dir_list)):
        if (dir_list[i] in os.listdir()) != True:
            os.mkdir(dir_list[i])
    path_wav = os.getcwd() + '/wav/'
    path_mp3 = os.getcwd() + '/mp3/'
    path_mp4 = os.getcwd() + '/mp4/'
    final_movie = os.getcwd() + '/final_movie/'
    path_ffmpeg = os.getcwd() + '/ffmpeg/'
    ffmpeg_optimized = os.getcwd() + '/ffmpeg_optimized/'
    
    macbook_Speaker(sentences, path_wav, source_language, target_language, speaker_wav_dir) # gender
    
    # 8 -----------------------------------------
    
    wav_to_mp3(sentences, path_wav, path_mp3)
    
    # 9 -----------------------------------------
    
    save_Subclips(sentences, start, end, videoclip, path_mp4, video_path_concat)
    
    # 10 -----------------------------------------
    
    change_ClipSpeed(start, path_ffmpeg, ffmpeg_optimized, path_mp3, path_mp4, path_wav, final_movie)
    
    # 11 -----------------------------------------
    
    video_bosluklari = clip_Parts_without_speaker(start, end, videoclip)
    
    # 12 -----------------------------------------
    
    remove_and_setting_video_ends(start, video_bosluklari, final_movie, path_mp4)
    
    # 13 -----------------------------------------
    
    final = concatenate_All_Clip_Parts(video_bosluklari)
    
    # 14 -----------------------------------------
    final_video_name = os.path.basename(video_path_concat)
    final_video_path, final__full_video_path = save_Final_Video(final, final_video_name)
    
    # 15 -----------------------------------------
    
    concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path)
    

if __name__ == '__main__':
    main()





# =============================================================================
# num_2_word_list = list()
# for xx in range(len(31)):
#     print(transcript[removable_index[xx]].get('word'))
#     num_2_word_list.append()
# 
# 
# 
# 
# 
# 
# from num2words import num2words
# 
# try:
#      print(num2words(2025, lang='tr'))
# except NotImplementedError:
#      print(num2words(42, lang='en'))
# 
# 
# 
# 
# 
# 
# 
# 
# 
# from lingua_franca import text2num
# 
# input_text = "I paid $750,000 for the house."
# converted_text = text2num(input_text, lang='en_US')
# print(converted_text)
# 
# 
# =============================================================================




# import re

# text = "Merhaba, dünya! Bugün hava çok güzel. İyi günler dilerim."

# split_text = text.split(" ")
# final_sentence = list()
# for i in range(len(split_text)):
    
#     splitted_text = re.split(r'[^\w\s]', split_text[i])
#     cleaned_split_text = [word for word in splitted_text if word.strip() != '']
#     final_sentence.append(cleaned_split_text)

# print(final_sentence)


# set1 = set(list(final_sentence[0][0]))
# set2 = set(list(split_text[0]))

# different_elements = set2.difference(set1)
# print(different_elements)  # Output: {1, 2}


###########################################################



# for i in range(len(result("segments"))):
#     text = result("segments")[i]("text")
    
#     split_text = text.split(" ")
#     final_sentence = list()
#     for j in range(len(split_text)):
#         splitted_text = re.split(r'[^\w\s]', split_text[i])
#         cleaned_split_text = [word for word in splitted_text if word.strip() != '']
#         final_sentence.append(cleaned_split_text)
                              
    
# set1 = set(list(final_sentence[0][0]))
# set2 = set(list(split_text[0]))

# if len(list(final_sentence[0][0])) > len(list(split_text[0])):
#     different_elements = set1.difference(set2)
#     different_elements = list(different_elements)
# elif len(list(split_text[0])) > len(list(final_sentence[0][0])):
#     different_elements = set2.difference(set1)
#     different_elements = list(different_elements)
#     element_index = list(set2).index(different_elements[0])
#     if element_index > 0 :
        
# else:
#     pass
    


#from time import time

# 1. visit hf.co/pyannote/speaker-diarization and accept user conditions
# 2. visit hf.co/pyannote/segmentation and accept user conditions
# 3. visit hf.co/settings/tokens to create an access token
# 4. instantiate pretrained speaker diarization pipeline
# =============================================================================
# from pyannote.audio import Pipeline
# pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization@2.1")
# 
# start = time()
# for i in range(1,129):
# 
#     # apply the pipeline to an audio file
#     diarization = pipeline(f"/Users/kadirbarut/test_node/F-Augmenter_last/vocal-remover/wav/{i}.wav")
#     
#     # dump the diarization output to disk using RTTM format
#     with open(f"/Users/kadirbarut/test_node/F-Augmenter_last/vocal-remover/pyannote_diarization/wav_{i}.rttm", "w") as rttm:
#         diarization.write_rttm(rttm)
# end = time()
# 
# 
# 
# 
# 
# 
# import torch
# from TTS.api import TTS
# 
# tts = TTS(model_name="voice_conversion_models/multilingual/vctk/freevc24", progress_bar=False).to("cpu")
# tts.voice_conversion_to_file(source_wav="/Users/kadirbarut/Desktop/sorun_var_short_test.wav", target_wav="/Users/kadirbarut/Downloads/zekiye_(enhanced).wav", file_path="/Users/kadirbarut/Desktop/sorun_var_zekiye.wav")
# 
# 
# 
# 
# from scipy.io import wavfile
# import librosa
# import crepe
# sr, audio = wavfile.read("/Users/kadirbarut/Desktop/sorun_var_short.wav")
# time, frequency, confidence, activation = crepe.predict(audio, sr, viterbi=True)
# 
# 
# 
# 
# model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to('cpu')
# import rubberband
# synth_vocals, sr = model.tts("""Geçen yıllar, geçerken uğradılar yine geçen de
#                                  Onlar anlatırken içim geçer de
#                                  Bu içimden geçenler gelir geçer de
#                                 Geç bunları içim desem de, geç kalınmış bazı şeyler için de""", language='tr')
# rubberband = rubberband.RubberBandStretcher(synth_vocals, sr)
# for i in range(len(frequency)):
#     rubberband.set_pitch_scale(frequency[i])
# shifted_vocals = rubberband.stretch()
# 
# 
# 
# 
# 
# =============================================================================
