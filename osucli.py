#!/usr/bin/env python3
import os
import time
import curses
import math
import threading
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Set
from enum import Enum

# ---------------- CONFIG ----------------
OSU_FILE = "t+pazolite - Oshama Scramble! ([ A v a l o n ]) [EXPERT].osu"

# Key bindings for different key modes
DEFAULT_BINDS = {
    4: ['d', 'f', 'j', 'k'],
    5: ['d', 'f', ' ', 'j', 'k'],
    6: ['s', 'd', 'f', 'j', 'k', 'l'],
    7: ['s', 'd', 'f', ' ', 'j', 'k', 'l'],
    8: ['a', 's', 'd', 'f', 'j', 'k', 'l', ';'],
}

# Hit windows (ms) - osu!mania style
HIT_WINDOW_PERFECT = 16   # 320 points
HIT_WINDOW_GREAT = 40     # 300 points  
HIT_WINDOW_GOOD = 73      # 200 points
HIT_WINDOW_BAD = 103      # 100 points
HIT_WINDOW_MISS = 127     # 0 points
HOLD_RELEASE_WINDOW = 100

# Scoring values
SCORE_PERFECT = 320
SCORE_GREAT = 300
SCORE_GOOD = 200
SCORE_BAD = 100
SCORE_MISS = 0

# Visuals
SCROLL_MS_PER_ROW = 24   # Default scroll speed (lower = faster)
LANE_WIDTH = 6
NOTE_HEAD = "O"          # Use ASCII for better compatibility
NOTE_BODY = "|"
HOLD_HEAD = "H"
HOLD_BODY = "|"
HIT_LINE_CHAR = "="
LANE_DIVIDER = "|"
HIT_EFFECT_CHARS = ["*", "+", ".", " "]

# Audio alternatives
AUDIO_OFFSET_MS = 0
USE_BEEP = False  # Use terminal beep for audio feedback
# ----------------------------------------

class Judgment(Enum):
    PERFECT = "PERFECT"
    GREAT = "GREAT"
    GOOD = "GOOD"
    BAD = "BAD"
    MISS = "MISS"

@dataclass
class HitEffect:
    lane: int
    time_created: int
    judgment: Judgment
    duration: int = 400  # ms

@dataclass
class Note:
    time: int
    lane: int
    kind: str  # "normal" or "hold"
    end_time: Optional[int] = None
    judged: bool = False
    hit: bool = False
    hold_active: bool = False
    hold_judged: bool = False

@dataclass
class Stats:
    score: int = 0
    combo: int = 0
    max_combo: int = 0
    perfect: int = 0
    great: int = 0
    good: int = 0
    bad: int = 0
    miss: int = 0
    total_notes: int = 0
    
    @property
    def accuracy(self) -> float:
        if self.total_notes == 0:
            return 100.0
        total_value = (self.perfect * 320 + self.great * 300 + 
                      self.good * 200 + self.bad * 100 + self.miss * 0)
        max_value = self.total_notes * 320
        return (total_value / max_value) * 100 if max_value > 0 else 100.0

class SimpleAudioManager:
    """Simple audio manager using system tools"""
    def __init__(self):
        self.audio_file = None
        self.audio_process = None
        self.start_time = None
        
    def load_music(self, filepath: str) -> bool:
        if os.path.exists(filepath):
            self.audio_file = filepath
            return True
        return False
    
    def play(self):
        if self.audio_file:
            try:
                # Try to use common Linux audio players
                import subprocess
                # Try different audio players available on most Linux systems
                players = ['mpv', 'mplayer', 'aplay', 'paplay']
                
                for player in players:
                    try:
                        if player == 'mpv':
                            self.audio_process = subprocess.Popen([
                                'mpv', '--no-video', '--quiet', self.audio_file
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        elif player == 'mplayer':
                            self.audio_process = subprocess.Popen([
                                'mplayer', '-quiet', self.audio_file
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            continue
                        
                        self.start_time = time.time()
                        return
                    except (FileNotFoundError, subprocess.SubprocessError):
                        continue
            except ImportError:
                pass
    
    def is_playing(self) -> bool:
        if self.audio_process:
            return self.audio_process.poll() is None
        return False

# ---------- .osu parsing ----------
def parse_osu_file(path: str):
    sections = {}
    current = None
    try:
        with open(path, encoding="utf-8", errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1]
                    sections[current] = []
                else:
                    if current:
                        sections[current].append(line)
    except Exception as e:
        raise Exception(f"Failed to parse .osu file: {e}")
    return sections

def parse_hitobjects(hitobject_lines: List[str], keys: int) -> List[Note]:
    notes = []
    lane_width = 512 / keys
    
    for line in hitobject_lines:
        parts = line.split(",")
        if len(parts) < 5:
            continue
            
        try:
            x = int(parts[0])
            t = int(parts[2])
            obj_type = int(parts[3])
            
            # Calculate lane (1-indexed)
            lane = int(x / lane_width) + 1
            lane = max(1, min(keys, lane))

            if obj_type & 128:  # Long note (hold)
                if len(parts) > 5:
                    extras = parts[5].split(":")
                    end_time = int(extras[0])
                    notes.append(Note(time=t, lane=lane, kind="hold", end_time=end_time))
            else:  # Normal note
                notes.append(Note(time=t, lane=lane, kind="normal"))
        except (ValueError, IndexError):
            continue  # Skip malformed lines
    
    notes.sort(key=lambda n: (n.time, n.lane))
    return notes

# ---------- Judgment System ----------
def calculate_judgment(delta: int) -> Judgment:
    """Calculate judgment based on timing delta"""
    d = abs(delta)
    if d <= HIT_WINDOW_PERFECT:
        return Judgment.PERFECT
    elif d <= HIT_WINDOW_GREAT:
        return Judgment.GREAT
    elif d <= HIT_WINDOW_GOOD:
        return Judgment.GOOD
    elif d <= HIT_WINDOW_BAD:
        return Judgment.BAD
    else:
        return Judgment.MISS

def get_score_for_judgment(judgment: Judgment) -> int:
    return {
        Judgment.PERFECT: SCORE_PERFECT,
        Judgment.GREAT: SCORE_GREAT,
        Judgment.GOOD: SCORE_GOOD,
        Judgment.BAD: SCORE_BAD,
        Judgment.MISS: SCORE_MISS
    }[judgment]

def update_stats(stats: Stats, judgment: Judgment):
    """Update stats based on judgment"""
    stats.total_notes += 1
    score = get_score_for_judgment(judgment)
    stats.score += score
    
    if judgment == Judgment.PERFECT:
        stats.perfect += 1
        stats.combo += 1
    elif judgment == Judgment.GREAT:
        stats.great += 1
        stats.combo += 1
    elif judgment == Judgment.GOOD:
        stats.good += 1
        stats.combo += 1
    elif judgment == Judgment.BAD:
        stats.bad += 1
        stats.combo += 1
    else:  # MISS
        stats.miss += 1
        stats.combo = 0
    
    stats.max_combo = max(stats.max_combo, stats.combo)

# ---------- Audio Feedback ----------
def play_hit_sound(judgment: Judgment):
    """Play audio feedback using system beep or other methods"""
    if not USE_BEEP:
        return
        
    try:
        if judgment in [Judgment.PERFECT, Judgment.GREAT]:
            # High pitched beep for good hits
            os.system("echo -e '\a' > /dev/null 2>&1")
        elif judgment == Judgment.MISS:
            # Different sound for miss (if possible)
            pass
    except:
        pass

# ---------- Rendering ----------
def get_note_y_position(note_time: int, current_time: int, hit_line_y: int) -> int:
    """Calculate note Y position based on scroll speed"""
    time_diff = note_time - current_time
    offset = int(time_diff / SCROLL_MS_PER_ROW)
    return hit_line_y - offset

def draw_note(stdscr, note: Note, x: int, y: int, current_time: int, play_height: int, hit_line_y: int):
    """Draw a single note"""
    if note.kind == "normal":
        if 0 <= y < play_height:
            try:
                # Add highlighting for approaching notes
                time_until_hit = note.time - current_time
                if abs(time_until_hit) <= HIT_WINDOW_GREAT:
                    stdscr.addstr(y, x, NOTE_HEAD.center(LANE_WIDTH), curses.A_BOLD)
                else:
                    stdscr.addstr(y, x, NOTE_HEAD.center(LANE_WIDTH))
            except curses.error:
                pass
    else:  # hold note
        if note.end_time:
            end_y = get_note_y_position(note.end_time, current_time, hit_line_y)
            y_start = min(y, end_y)
            y_end = max(y, end_y)
            
            # Draw hold body
            for yy in range(max(0, y_start), min(play_height, y_end + 1)):
                try:
                    if yy == y:  # Head
                        stdscr.addstr(yy, x, HOLD_HEAD.center(LANE_WIDTH), curses.A_BOLD)
                    elif yy == end_y:  # Tail
                        stdscr.addstr(yy, x, HOLD_HEAD.center(LANE_WIDTH))
                    else:  # Body
                        attr = curses.A_REVERSE if note.hold_active else 0
                        stdscr.addstr(yy, x, HOLD_BODY.center(LANE_WIDTH), attr)
                except curses.error:
                    pass

def draw_hit_effects(stdscr, effects: List[HitEffect], current_time: int, start_x: int, hit_y: int):
    """Draw hit effects"""
    for effect in effects[:]:
        age = current_time - effect.time_created
        if age > effect.duration:
            effects.remove(effect)
            continue
            
        # Animate effect
        progress = age / effect.duration
        char_idx = min(len(HIT_EFFECT_CHARS) - 1, int(progress * len(HIT_EFFECT_CHARS)))
        char = HIT_EFFECT_CHARS[char_idx]
        
        if char == " ":  # Effect finished
            continue
            
        lane_x = start_x + (effect.lane - 1) * (LANE_WIDTH + 1)
        
        try:
            # Different attributes for different judgments
            attr = curses.A_BOLD
            if effect.judgment == Judgment.PERFECT:
                attr |= curses.A_STANDOUT
            elif effect.judgment == Judgment.MISS:
                attr = curses.A_DIM
                
            stdscr.addstr(hit_y - 1, lane_x + LANE_WIDTH//2, char, attr)
        except curses.error:
            pass

def draw_frame(stdscr, keys: int, notes: List[Note], current_time: int, stats: Stats, 
              held: Dict[int, bool], effects: List[HitEffect], scroll_multiplier: float):
    """Draw the main game frame"""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    
    # Ensure minimum terminal size
    if h < 20 or w < 30:
        stdscr.addstr(0, 0, "Terminal too small! Need at least 30x20")
        stdscr.refresh()
        return
        
    play_height = h - 7
    start_x = max(0, (w - (keys * LANE_WIDTH + (keys - 1))) // 2)
    hit_line_y = play_height - 3

    # Draw lanes and highlighting for held keys
    for lane in range(1, keys + 1):
        lane_x = start_x + (lane - 1) * (LANE_WIDTH + 1)
        
        # Lane divider
        if lane > 1 and lane_x > 0:
            for yy in range(play_height):
                try:
                    stdscr.addstr(yy, lane_x - 1, LANE_DIVIDER)
                except curses.error:
                    pass
        
        # Highlight held lanes
        if held.get(lane, False):
            for yy in range(play_height):
                try:
                    if lane_x >= 0 and lane_x + LANE_WIDTH < w:
                        stdscr.addstr(yy, lane_x, " " * LANE_WIDTH, curses.A_REVERSE)
                except curses.error:
                    pass

    # Draw notes
    visible_notes = 0
    for note in notes:
        if note.judged and note.kind == "normal":
            continue
        if note.kind == "hold" and note.judged and note.hold_judged:
            continue
            
        y = get_note_y_position(note.time, current_time, hit_line_y)
        lane_x = start_x + (note.lane - 1) * (LANE_WIDTH + 1)
        
        # Only draw notes that are visible
        if -5 <= y <= play_height + 5 and lane_x >= 0:
            draw_note(stdscr, note, lane_x, y, current_time, play_height, hit_line_y)
            visible_notes += 1

    # Draw hit line
    try:
        if start_x >= 0 and start_x + (keys * LANE_WIDTH + (keys - 1)) < w:
            hit_line = HIT_LINE_CHAR * (keys * LANE_WIDTH + (keys - 1))
            stdscr.addstr(hit_line_y, start_x, hit_line, curses.A_BOLD)
    except curses.error:
        pass

    # Draw hit effects
    draw_hit_effects(stdscr, effects, current_time, start_x, hit_line_y)

    # Draw UI
    try:
        # Score and combo
        stdscr.addstr(play_height, 0, f"Score: {stats.score:,}")
        stdscr.addstr(play_height + 1, 0, f"Combo: {stats.combo} (Max: {stats.max_combo})")
        
        # Accuracy and judgment counts
        acc_line = f"Accuracy: {stats.accuracy:.2f}%"
        stdscr.addstr(play_height + 2, 0, acc_line)
        
        judgment_line = f"P:{stats.perfect} G:{stats.great} Good:{stats.good} Bad:{stats.bad} M:{stats.miss}"
        if len(judgment_line) < w - 1:
            stdscr.addstr(play_height + 3, 0, judgment_line)
        
        # Controls
        key_display = [k if k != ' ' else 'SPC' for k in DEFAULT_BINDS.get(keys, ['d', 'f', 'j', 'k'])]
        controls = f"Keys: {' '.join(key_display)} | Q:quit +/-:speed"
        if len(controls) < w - 1:
            stdscr.addstr(play_height + 4, 0, controls)
            
        # Debug info
        stdscr.addstr(play_height + 5, 0, f"Notes visible: {visible_notes} | Speed: {scroll_multiplier:.1f}x")
    except curses.error:
        pass
    
    stdscr.refresh()

# ---------- Game Logic ----------
def handle_note_hit(note: Note, current_time: int, stats: Stats, effects: List[HitEffect]) -> Judgment:
    """Handle hitting a note head"""
    if note.judged:
        return Judgment.MISS
        
    delta = current_time - note.time
    judgment = calculate_judgment(delta)
    
    note.judged = True
    note.hit = judgment != Judgment.MISS
    
    if note.kind == "hold" and judgment != Judgment.MISS:
        note.hold_active = True
    
    update_stats(stats, judgment)
    effects.append(HitEffect(note.lane, current_time, judgment))
    
    # Audio feedback
    play_hit_sound(judgment)
    
    return judgment

def handle_hold_release(note: Note, current_time: int, stats: Stats, effects: List[HitEffect]):
    """Handle releasing a hold note"""
    if not note.hold_active or note.hold_judged:
        return
        
    if note.end_time:
        delta = current_time - note.end_time
        if abs(delta) <= HOLD_RELEASE_WINDOW:
            judgment = calculate_judgment(delta)
            update_stats(stats, judgment)
            effects.append(HitEffect(note.lane, current_time, judgment))
        else:
            # Missed release
            update_stats(stats, Judgment.MISS)
            effects.append(HitEffect(note.lane, current_time, Judgment.MISS))
    
    note.hold_judged = True
    note.hold_active = False

def check_missed_notes(notes: List[Note], current_time: int, stats: Stats, effects: List[HitEffect]):
    """Check for notes that have been missed"""
    for note in notes:
        if not note.judged and current_time - note.time > HIT_WINDOW_MISS:
            note.judged = True
            update_stats(stats, Judgment.MISS)
            effects.append(HitEffect(note.lane, current_time, Judgment.MISS))
        
        # Check for missed hold releases
        if (note.kind == "hold" and note.hold_active and not note.hold_judged 
            and note.end_time and current_time - note.end_time > HOLD_RELEASE_WINDOW):
            handle_hold_release(note, current_time, stats, effects)

def run_game(stdscr):
    """Main game loop - pure curses implementation"""
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(1)  # 1ms timeout for responsive input

    # Initialize colors if available
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()

    # Parse beatmap
    try:
        sections = parse_osu_file(OSU_FILE)
    except Exception as e:
        stdscr.addstr(0, 0, f"Error: {str(e)}")
        stdscr.addstr(1, 0, "Press any key to exit...")
        stdscr.getch()
        return

    # Get key count
    keys = 4  # Default to 4K
    if "Difficulty" in sections:
        for line in sections["Difficulty"]:
            if line.startswith("CircleSize"):
                try:
                    keys = int(float(line.split(":")[1]))
                except (ValueError, IndexError):
                    pass
                break

    # Parse notes
    notes = []
    if "HitObjects" in sections:
        notes = parse_hitobjects(sections["HitObjects"], keys)

    if not notes:
        stdscr.addstr(0, 0, "No notes found in beatmap!")
        stdscr.addstr(1, 0, "Press any key to exit...")
        stdscr.getch()
        return

    # Set up key bindings
    binds = DEFAULT_BINDS.get(keys, ['d', 'f', 'j', 'k'])
    keymap = {}
    for i, key_char in enumerate(binds):
        if isinstance(key_char, str):
            keymap[ord(key_char)] = i + 1
        else:
            keymap[key_char] = i + 1

    # Set up audio
    audio_manager = SimpleAudioManager()
    audio_loaded = False
    
    if "General" in sections:
        for line in sections["General"]:
            if line.startswith("AudioFilename"):
                audio_file = line.split(":", 1)[1].strip()
                song_file = os.path.join(os.path.dirname(OSU_FILE), audio_file)
                audio_loaded = audio_manager.load_music(song_file)
                break

    # Game state
    stats = Stats()
    effects = []
    game_started = False
    scroll_speed_multiplier = 1.0
    held = {i: False for i in range(1, keys + 1)}
    key_hold_times = {}  # Track how long keys are held

    # Show ready screen
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, f"osu!mania {keys}K (Pure Curses Edition)")
        key_display = [k if k != ' ' else 'SPACE' for k in binds]
        stdscr.addstr(1, 0, f"Keys: {' | '.join(key_display)}")
        stdscr.addstr(2, 0, f"Notes: {len(notes)}")
        stdscr.addstr(3, 0, f"Audio: {'Loaded' if audio_loaded else 'Not available'}")
        stdscr.addstr(5, 0, "Controls:")
        stdscr.addstr(6, 0, "  SPACE - Start game")
        stdscr.addstr(7, 0, "  Q - Quit")
        stdscr.addstr(8, 0, "  +/- - Adjust scroll speed")
        stdscr.addstr(10, 0, "Press SPACE to start!")
        stdscr.refresh()
    except curses.error:
        pass

    # Wait for start
    while True:
        key = stdscr.getch()
        if key == ord('q') or key == ord('Q'):
            return
        elif key == ord(' '):
            break
        time.sleep(0.01)

    # Start game
    start_time = time.time() * 1000
    if audio_loaded:
        audio_manager.play()
    
    running = True
    last_frame_time = time.time()

    while running:
        current_frame_time = time.time()
        current_time = int(time.time() * 1000 - start_time) + AUDIO_OFFSET_MS

        # Handle all available input
        while True:
            key = stdscr.getch()
            if key == -1:  # No more input
                break
                
            if key == ord('q') or key == ord('Q'):
                running = False
                break
            elif key == ord('+') or key == ord('='):
                scroll_speed_multiplier = min(3.0, scroll_speed_multiplier + 0.1)
                global SCROLL_MS_PER_ROW
                SCROLL_MS_PER_ROW = max(1, int(12 / scroll_speed_multiplier))
            elif key == ord('-') or key == ord('_'):
                scroll_speed_multiplier = max(0.3, scroll_speed_multiplier - 0.1)
                SCROLL_MS_PER_ROW = max(1, int(12 / scroll_speed_multiplier))
            elif key in keymap:
                lane = keymap[key]
                
                # Handle key press
                if not held[lane]:  # New press
                    held[lane] = True
                    key_hold_times[lane] = current_time
                    
                    # Find hittable note in this lane
                    best_note = None
                    best_delta = float('inf')
                    
                    for note in notes:
                        if (note.lane == lane and not note.judged):
                            delta = abs(current_time - note.time)
                            if delta <= HIT_WINDOW_MISS and delta < best_delta:
                                best_note = note
                                best_delta = delta
                    
                    if best_note:
                        handle_note_hit(best_note, current_time, stats, effects)
                else:
                    # Key being held - update hold time
                    key_hold_times[lane] = current_time

        # Auto-release keys that haven't been pressed recently (simulate key up)
        release_timeout = 100  # ms
        for lane in list(key_hold_times.keys()):
            if current_time - key_hold_times[lane] > release_timeout:
                if held[lane]:
                    held[lane] = False
                    # Handle hold note releases
                    for note in notes:
                        if (note.lane == lane and note.kind == "hold" and 
                            note.hold_active and not note.hold_judged):
                            handle_hold_release(note, current_time, stats, effects)
                del key_hold_times[lane]

        # Check for missed notes
        check_missed_notes(notes, current_time, stats, effects)

        # Render frame
        draw_frame(stdscr, keys, notes, current_time, stats, held, effects, scroll_speed_multiplier)

        # Check if all notes are finished
        all_judged = all(note.judged and (note.kind != "hold" or note.hold_judged) 
                        for note in notes)
        if all_judged:
            time.sleep(2)  # Show final state for a moment
            break

        # Check if audio finished (approximate)
        if audio_loaded and audio_manager.start_time:
            # Simple time-based check since we can't reliably check if audio is playing
            elapsed = (time.time() - audio_manager.start_time)
            if elapsed > 300:  # 5 minutes max - adjust as needed
                if all_judged:
                    break

        # Maintain framerate
        frame_time = time.time() - current_frame_time
        sleep_time = max(0, 1/60 - frame_time)
        time.sleep(sleep_time)

    # Show results
    show_results(stdscr, stats, keys)

def show_results(stdscr, stats: Stats, keys: int):
    """Show final results screen"""
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, "=" * 35)
        stdscr.addstr(1, 0, "         GAME COMPLETE!")
        stdscr.addstr(2, 0, "=" * 35)
        
        stdscr.addstr(4, 0, f"Final Score: {stats.score:,}")
        stdscr.addstr(5, 0, f"Max Combo: {stats.max_combo}")
        stdscr.addstr(6, 0, f"Accuracy: {stats.accuracy:.2f}%")
        
        stdscr.addstr(8, 0, "Judgment Breakdown:")
        stdscr.addstr(9, 0, f"  Perfect: {stats.perfect:4d}")
        stdscr.addstr(10, 0, f"  Great:   {stats.great:4d}")
        stdscr.addstr(11, 0, f"  Good:    {stats.good:4d}")
        stdscr.addstr(12, 0, f"  Bad:     {stats.bad:4d}")
        stdscr.addstr(13, 0, f"  Miss:    {stats.miss:4d}")
        stdscr.addstr(14, 0, f"  Total:   {stats.total_notes:4d}")
        
        # Calculate grade
        if stats.accuracy >= 95:
            grade = "S"
        elif stats.accuracy >= 90:
            grade = "A"
        elif stats.accuracy >= 80:
            grade = "B"
        elif stats.accuracy >= 70:
            grade = "C"
        else:
            grade = "D"
            
        stdscr.addstr(16, 0, f"Grade: {grade}", curses.A_BOLD | curses.A_STANDOUT)
        
        stdscr.addstr(18, 0, "Press any key to exit...")
        stdscr.refresh()
        
        # Wait for key press
        stdscr.nodelay(False)
        stdscr.getch()
    except curses.error:
        pass

def test_keys(stdscr):
    """Test key detection"""
    stdscr.nodelay(True)
    stdscr.timeout(1)
    
    stdscr.addstr(0, 0, "Key Test Mode")
    stdscr.addstr(1, 0, "Press keys to test detection (Q to quit)")
    stdscr.addstr(3, 0, "Expected 4K keys: d, f, j, k")
    
    row = 5
    while True:
        key = stdscr.getch()
        if key == -1:
            continue
        if key == ord('q') or key == ord('Q'):
            break
            
        stdscr.addstr(row, 0, f"Key pressed: {key} ({chr(key) if 32 <= key <= 126 else 'special'})")
        row += 1
        if row > 20:
            stdscr.erase()
            stdscr.addstr(0, 0, "Key Test Mode")
            stdscr.addstr(1, 0, "Press keys to test detection (Q to quit)")
            row = 3
        stdscr.refresh()

def main():
    """Main entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "--test-keys":
        print("Starting key test mode...")
        curses.wrapper(test_keys)
        return
        
    if not os.path.exists(OSU_FILE):
        print(f"Error: Beatmap file not found:")
        print(f"{OSU_FILE}")
        print("\nPlease update the OSU_FILE path in the script.")
        print("You can also test key detection with: python3 script.py --test-keys")
        return
    
    print("osu!mania Clone (Pure Python Edition)")
    print("=" * 40)
    print("This version uses only standard Python libraries")
    print("No pygame dependency required!")
    print()
    print("Audio support:")
    print("- Tries to use system audio players (mpv, mplayer)")
    print("- Falls back to terminal beeps")
    print()
    print("Controls will be shown in game")
    print("Press Enter to continue...")
    input()
    
    try:
        curses.wrapper(run_game)
    except KeyboardInterrupt:
        print("\nGame interrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure the .osu file path is correct")
        print("2. Check terminal size (need at least 30x20)")
        print("3. Test keys with: python3 script.py --test-keys")
        print("4. For audio install: sudo apt install mpv")

if __name__ == "__main__":
    main()
