import pickle
import os
import sys
import argparse
import json
import shutil
import wave
from pathlib import Path
from tqdm import tqdm
from fsorter import fsorter
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
from subtitle_utils import load_subtitles
from voxcpm_runtime import (
    VOXCPM_OUTPUT_SAMPLE_RATE,
    VOXCPM_REFERENCE_SAMPLE_RATE,
    load_model,
    synthesize_cloned_speech,
)

BASE_DIR = Path(__file__).resolve().parent
VOCAL_REMOVER_DIR = BASE_DIR / "vocal-remover"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
PYTHON = sys.executable
MOVIEPY_DURATION_EPSILON = 0.05
MIN_CLIP_DURATION = 0.04


def run_command(args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def ensure_ffmpeg():
    if not FFMPEG or not Path(FFMPEG).exists():
        raise RuntimeError(
            "FFmpeg was not found. Install FFmpeg and try again."
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
        raise FileNotFoundError(f"Video file not found: {video_path}")

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
            f"Vocal-remover model is missing: {model_path}\n"
            "Run: python scripts/setup_models.py"
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
            end.append(loaded_transcript[i].get('end'))
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


def create_Speaker_Reference_Clips(start, end, vocal_audio_path):
    speaker_reference_dir = BASE_DIR / "speaker_reference_clips"
    speaker_reference_dir.mkdir(exist_ok=True)
    for i in range(len(start)):
        output_path = speaker_reference_dir / f"{i+1}.wav"
        duration = safe_float(end[i]) - safe_float(start[i])
        if duration < MIN_CLIP_DURATION:
            raise RuntimeError(f"Speaker reference segment {i + 1} is too short: {duration:.3f}s")
        run_command(
            [
                ensure_ffmpeg(),
                "-y",
                "-ss",
                start[i],
                "-t",
                duration,
                "-i",
                vocal_audio_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                VOXCPM_REFERENCE_SAMPLE_RATE,
                "-c:a",
                "pcm_s16le",
                output_path,
            ]
        )
    return str(speaker_reference_dir) + "/"
    
    
def generate_dubbed_speech(sentences, path_wav, source_language, target_language, speaker_wav_dir, pre_translated=False):
    translator = None
    if not pre_translated:
        from deep_translator import GoogleTranslator

        translator_source = source_language if source_language is not None else "auto"
        translator = GoogleTranslator(source=translator_source, target=target_language)
    print("Loading pinned VoxCPM2 voice-cloning model...", flush=True)
    model = load_model()
    for i in tqdm(range(len(sentences))):
        source_text = sentences[i].strip()
        text = source_text if pre_translated else translator.translate(source_text)
        if not text or not text.strip():
            raise RuntimeError(f"Translation returned empty text for segment {i + 1}.")
        print(f"Segment {i + 1}: {source_text!r} -> {text.strip()!r}", flush=True)
        wav_file = f"{path_wav}{i+1}.wav"
        speaker_wav = Path(speaker_wav_dir) / f"{i+1}.wav"
        synthesize_cloned_speech(
            model=model,
            text=text,
            reference_wav_path=speaker_wav,
            output_wav_path=wav_file,
            prompt_text=None if pre_translated else source_text,
            seed=42 + i,
        )


def save_Subclips(sentences, start, end, videoclip, path_mp4, video_path_concat): #  10
    for i in tqdm(range(len(sentences))):
        run_command([
            ensure_ffmpeg(), "-y", "-i", video_path_concat,
            "-ss", start[i], "-to", end[i],
            "-c:v", "libx264", "-c:a", "aac", f"{path_mp4}{i+1}.mp4",
        ])
        

def media_duration(path):
    if not FFPROBE or not Path(FFPROBE).exists():
        raise RuntimeError("ffprobe was not found. Install FFmpeg and try again.")
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def atempo_filter(tempo):
    if tempo <= 0:
        raise ValueError(f"Audio tempo must be positive, got {tempo}.")
    factors = []
    while tempo > 2.0:
        factors.append(2.0)
        tempo /= 2.0
    while tempo < 0.5:
        factors.append(0.5)
        tempo /= 0.5
    factors.append(tempo)
    return ",".join(f"atempo={factor:.8f}" for factor in factors)


def change_ClipSpeed(start, path_ffmpeg, path_mp4, path_wav, final_movie): #  11
    sort_wav = fsorter.fileSort(path_wav, ['.wav'])    
    sort_mp4 = fsorter.fileSort(path_mp4, ['.mp4'])
    for i in tqdm(range(len(start))):
        video_path = Path(path_mp4) / sort_mp4[i]
        audio_path = Path(path_wav) / sort_wav[i]
        video_duration = media_duration(video_path)
        audio_duration = media_duration(audio_path)
        adjusted_audio_path = Path(path_ffmpeg) / sort_wav[i]
        tempo = audio_duration / video_duration
        run_command(
            [
                ensure_ffmpeg(), "-y", "-i", audio_path,
                "-filter:a", atempo_filter(tempo),
                "-ar", VOXCPM_OUTPUT_SAMPLE_RATE,
                "-ac", "1",
                "-c:a", "pcm_s24le",
                adjusted_audio_path,
            ]
        )
        output_path = Path(final_movie) / sort_mp4[i]
        run_command(
            [
                ensure_ffmpeg(), "-y", "-i", video_path, "-i", adjusted_audio_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", VOXCPM_OUTPUT_SAMPLE_RATE, "-ac", "1",
                "-af", "apad", "-t", f"{video_duration:.6f}",
                output_path,
            ]
        )
            
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
    final = concatenate_videoclips(video_bosluklari)
    return final


def save_Final_Video(final, final_video_name): #  16
    final_video_dir = Path(os.getcwd()) / "final_video"
    final_video_full_dir = final_video_dir / "final"
    final_video_dir.mkdir(exist_ok=True)
    final_video_full_dir.mkdir(exist_ok=True)
    final_video_path = str(final_video_dir / final_video_name)
    final__full_video_path = str(final_video_full_dir / final_video_name)
    final.write_videofile(
        final_video_path,
        codec="libx264",
        audio_codec="aac",
        audio_fps=VOXCPM_OUTPUT_SAMPLE_RATE,
        audio_bitrate="192k",
    )
    
    return final_video_path, final__full_video_path


def concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path, bg_volume=0.35): # 18
    run_command([
        ensure_ffmpeg(), "-y", "-i", final_video_path, "-i", instrumental_audio_path,
        "-c:v", "copy",
        "-filter_complex",
        "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "loudnorm=I=-20:TP=-2:LRA=11[dub];"
        f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={bg_volume}[bg];"
        "[dub][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:a", "aac", "-b:a", "192k", "-ar", VOXCPM_OUTPUT_SAMPLE_RATE,
        "-ac", "2", "-shortest", final__full_video_path,
    ])


def _wav_to_s16le_raw(wav_path, sample_rate):
    proc = subprocess.run(
        [ensure_ffmpeg(), "-v", "error", "-i", str(wav_path),
         "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout


def build_dubbed_audio(start, end, adjusted_wav_dir, source_video, output_path): #  14a
    sample_rate = VOXCPM_OUTPUT_SAMPLE_RATE
    source_duration = media_duration(source_video)
    with wave.open(str(output_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        cursor = 0.0
        for i in tqdm(range(len(start))):
            seg_start = max(0.0, safe_float(start[i]))
            seg_end = safe_float(end[i])
            if seg_start > cursor:
                out.writeframes(b"\x00\x00" * int((seg_start - cursor) * sample_rate))
                cursor = seg_start
            adjusted_wav = Path(adjusted_wav_dir) / f"{i + 1}.wav"
            raw = _wav_to_s16le_raw(adjusted_wav, sample_rate)
            max_samples = int(max(0.0, seg_end - seg_start) * sample_rate)
            if max_samples > 0 and len(raw) // 2 > max_samples:
                raw = raw[:max_samples * 2]
            out.writeframes(raw)
            cursor = seg_start + (len(raw) // 2) / sample_rate
        if source_duration > cursor:
            out.writeframes(b"\x00\x00" * int((source_duration - cursor) * sample_rate))
    return str(output_path)


def assemble_final_video(source_video, dubbed_audio_path, instrumental_audio_path, final_video_name, bg_volume=0.35): #  14b
    final_video_dir = Path(os.getcwd()) / "final_video"
    final_video_full_dir = final_video_dir / "final"
    final_video_dir.mkdir(exist_ok=True)
    final_video_full_dir.mkdir(exist_ok=True)
    final_video_path = str(final_video_dir / final_video_name)
    final__full_video_path = str(final_video_full_dir / final_video_name)
    run_command([
        ensure_ffmpeg(), "-y",
        "-i", source_video,
        "-i", dubbed_audio_path,
        "-i", instrumental_audio_path,
        "-filter_complex",
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "loudnorm=I=-20:TP=-2:LRA=11[dub];"
        f"[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={bg_volume}[bg];"
        "[dub][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(VOXCPM_OUTPUT_SAMPLE_RATE),
        "-ac", "2", "-shortest", final__full_video_path,
    ])
    return final_video_path, final__full_video_path


SOURCE_LANGUAGES = {
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
    'Czech (source only)': 'cs',
}

TARGET_LANGUAGES = {
    name: code
    for name, code in SOURCE_LANGUAGES.items()
    if code not in {"automatic", "cs"}
}


def normalize_language(value, allow_auto=False):
    if value is None:
        return None
    value = value.strip()
    if allow_auto and value.lower() in {"auto", "automatic", "auto detect"}:
        return None
    languages = SOURCE_LANGUAGES if allow_auto else TARGET_LANGUAGES
    for name, code in languages.items():
        if value.lower() == name.lower() or value.lower() == code.lower():
            return None if code == "automatic" else code
    valid = ", ".join(f"{name}={code}" for name, code in languages.items())
    raise ValueError(f"Invalid language: {value}. Valid values: {valid}")


def ask_language(prompt, allow_auto=False):
    languages = SOURCE_LANGUAGES if allow_auto else TARGET_LANGUAGES
    [print(element, ' ----> ', number + 1) for number, element in enumerate(list(languages.keys()))]
    selected_index = int(input(prompt))
    selected_code = list(languages.values())[selected_index - 1]
    return None if selected_code == "automatic" else selected_code


def parse_args():
    parser = argparse.ArgumentParser(description="Local-first video translation and dubbing pipeline")
    parser.add_argument("video", nargs="?", help="Path to the input video")
    parser.add_argument("--source-language", "-s", help="Source language code or name, for example: en, tr, auto")
    parser.add_argument("--target-language", "-t", help="Target language code or name, for example: en, tr")
    parser.add_argument(
        "--speaker-wav-dir",
        help="Directory containing 1.wav, 2.wav, etc. for VoxCPM2 speaker references. Generated from the source vocals when omitted.",
    )
    parser.add_argument(
        "--subtitles",
        help="Subtitle file (.srt/.vtt) with dialogue text and timing. Skips Whisper transcription. "
             "If --subtitle-language matches the target language, cues are spoken directly without translation.",
    )
    parser.add_argument(
        "--subtitle-language",
        help="Language code of the subtitle file (defaults to the target language)",
    )
    parser.add_argument(
        "--bg-volume",
        type=float,
        default=0.35,
        help="Background music/effects volume relative to the original track in the final mix (default: 0.35)",
    )
    parser.add_argument(
        "--low-memory",
        action="store_true",
        help="Memory-safe final assembly: the source video is converted to H.264 in one streaming pass "
             "(same output codec as the default path) instead of opening every subtitle segment in memory. "
             "Use for videos with many subtitle segments, where the default reconstruction can exhaust RAM.",
    )
    parser.add_argument("--list-languages", action="store_true", help="Print supported language codes")
    return parser.parse_args()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

    # 1 ---------------------------------------

    args = parse_args()
    if args.list_languages:
        print("VoxCPM2 target languages:")
        for name, code in TARGET_LANGUAGES.items():
            print(f"{name}: {code}")
        print("Source-only transcription languages:")
        print("Czech: cs")
        return

    print('\n\n'+' '*30 + '* VIDEO TRANSLATOR *\n\n')

    video_path = args.video or input("\nVideo file path: ").strip()
    source_language = normalize_language(args.source_language, allow_auto=True) if args.source_language else ask_language('\nPlease select the original video language number : ', allow_auto=True)
    target_language = normalize_language(args.target_language) if args.target_language else ask_language('\nPlease select the target video language number : ')
    subtitle_cues = None
    subtitle_language = None
    if args.subtitles:
        subtitle_cues = load_subtitles(args.subtitles)
        if not subtitle_cues:
            raise RuntimeError(f"No usable subtitle cues found in: {args.subtitles}")
        subtitle_language = normalize_language(args.subtitle_language) if args.subtitle_language else target_language
        print(f"Using subtitle file ({subtitle_language}): {len(subtitle_cues)} cues - Whisper transcription skipped")
    
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
    os.chdir(VOCAL_REMOVER_DIR)
    audio_stem = os.path.basename(os.path.splitext(audio_path)[0])
    vocal_audio_path = os.getcwd() + "/" + file_path + audio_stem +'_Vocals.wav'
    instrumental_audio_path = os.getcwd() + "/" + file_path + audio_stem +'_Instruments.wav'
    if subtitle_cues is None:
        os.chdir(BASE_DIR)
        if ('transcripts' in os.listdir()) != True:
            os.mkdir('transcripts')
        transcript_path = audio_stem
        transcript_path = str(os.getcwd() + '/transcripts/' + transcript_path)
        split_transcript_name = transcript_path.split('/')[-1]
        split_transcript_folder = transcript_path.split('/')
        split_transcript_folder.pop(-1)
        split_transcript_folder = '/'.join(split_transcript_folder) + str('/')
        if (split_transcript_name in os.listdir(split_transcript_folder)) == False:
            save_Transcript(vocal_audio_path, transcript_path, source_language)
        os.chdir(VOCAL_REMOVER_DIR)
        loaded_transcript = read_Transcript(transcript_path)
    else:
        loaded_transcript = None
    
    # 4 -----------------------------------------
    character_set = ['.', '!', '?']
    if subtitle_cues is not None:
        start = [cue[0] for cue in subtitle_cues]
        end = [cue[1] for cue in subtitle_cues]
        sentences = [cue[2] for cue in subtitle_cues]
    else:
        start, end = create_TimeStamps(loaded_transcript, character_set)
        sentences = create_Sentences(loaded_transcript, character_set)
    
    # 6 -----------------------------------------
    
    speaker_wav_dir = args.speaker_wav_dir
    if speaker_wav_dir is None:
        speaker_wav_dir = create_Speaker_Reference_Clips(start, end, vocal_audio_path)
    else:
        speaker_wav_dir = str(Path(speaker_wav_dir).expanduser().resolve()) + "/"
    # 7 -----------------------------------------
    
    dir_list = ['wav','mp4','final_movie','ffmpeg']
    for i in range(len(dir_list)):
        if (dir_list[i] in os.listdir()) != True:
            os.mkdir(dir_list[i])
    path_wav = os.getcwd() + '/wav/'
    path_mp4 = os.getcwd() + '/mp4/'
    final_movie = os.getcwd() + '/final_movie/'
    path_ffmpeg = os.getcwd() + '/ffmpeg/'
    
    pre_translated = subtitle_cues is not None and subtitle_language == target_language
    generate_dubbed_speech(sentences, path_wav, source_language, target_language, speaker_wav_dir, pre_translated=pre_translated)
    
    save_Subclips(sentences, start, end, videoclip, path_mp4, video_path_concat)
    
    # 10 -----------------------------------------
    
    change_ClipSpeed(start, path_ffmpeg, path_mp4, path_wav, final_movie)
    
    final_video_name = os.path.basename(video_path_concat)
    if args.low_memory:
        dubbed_audio_path = build_dubbed_audio(
            start, end, path_ffmpeg, video_path_concat,
            str(Path(os.getcwd()) / "wav" / (Path(final_video_name).stem + "_dubbed.wav")))
        final_video_path, final__full_video_path = assemble_final_video(
            video_path_concat, dubbed_audio_path, instrumental_audio_path,
            final_video_name, args.bg_volume)
    else:
        # 11 -----------------------------------------

        video_bosluklari = clip_Parts_without_speaker(start, end, videoclip)

        # 12 -----------------------------------------

        remove_and_setting_video_ends(start, video_bosluklari, final_movie, path_mp4)

        # 13 -----------------------------------------

        final = concatenate_All_Clip_Parts(video_bosluklari)

        # 14 -----------------------------------------

        final_video_path, final__full_video_path = save_Final_Video(final, final_video_name)

        # 15 -----------------------------------------

        concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path, args.bg_volume)
    

if __name__ == '__main__':
    main()
