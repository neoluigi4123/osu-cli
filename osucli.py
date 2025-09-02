#!/usr/bin/env python3
import os
import time
import curses
import math
import threading
import queue
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from enum import Enum

# ---------------- CONFIG ----------------
OSU_FILE = "t+pazolite - Oshama Scramble! ([ A v a l o n ]) [EXPERT].osu"

# Key bindings for different key modes (using curses key codes)
DEFAULT_BINDS = {
    4: [ord('d'), ord('f'), ord('j'), ord('k')],
    5: [ord('d'), ord('f'), ord(' '), ord('j'), ord('k')],
    6: [ord('s'), ord('d'), ord('f'), ord('j'), ord('k'), ord('l')],
    7: [ord('s'), ord('d'), ord('f'), ord(' '), ord('j'), ord('k'), ord('l')],
    8: [ord('a'), ord('s'), ord('d'), ord('f'), ord('j'), ord('k'), ord('l'), ord(';')],
}

# Hit windows (ms) - more accurate to osu!mania
HIT_WINDOW_PERFECT = 16   # 300 (Perfect)
HIT_WINDOW_GREAT = 40     # 300 (Great)
HIT_WINDOW_GOOD = 73      # 200 (Good)
HIT_WINDOW_BAD = 103      # 100 (Bad)
HIT_WINDOW_MISS = 127     # 50 (Miss)
HOLD_RELEASE_WINDOW = 100

# Scoring values
SCORE_PERFECT = 320
SCORE_GREAT = 300
SCORE_GOOD = 200
SCORE_BAD = 100
SCORE_MISS = 0

# Visuals
SCROLL_SPEED = 500        # pixels per second
LANE_WIDTH = 6           # wider lanes for better visibility
NOTE_HEAD = "●"
NOTE_BODY = "█"
HOLD_HEAD = "◆"
HOLD_BODY = "█"
HIT_LINE_CHAR = "━"
LANE_DIVIDER = "│"
HIT_EFFECT_CHARS = ["★", "✦", "✧", "·"]

# Audio
AUDIO_OFFSET_MS = 0      # Global audio offset
SCROLL_MS_PER_ROW = 12   # Default scroll speed
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
    duration: int = 300  # ms

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

class InputHandler:
    """Handle input using curses with key state tracking"""
    def __init__(self, stdscr, keys: int):
        self.stdscr = stdscr
        self.keys = keys
        self.binds = DEFAULT_BINDS.get(keys, [ord('d'), ord('f'), ord('j'), ord('k')])
        self.held = {i: False for i in range(1, keys + 1)}
        self.keymap = {key_code: i + 1 for i, key_code in enumerate(self.binds)}
        self.key_states = {}
        self.last_update = time.time() * 1000
        
    def update(self, current_time: int) -> Tuple[List[int], List[int]]:
        """Returns (pressed_lanes, released_lanes)"""
        pressed = []
        released = []
        
        # Get all available input
        while True:
            key = self.stdscr.getch()
            if key == -1:  # No more input
                break
                
            if key in self.keymap:
                lane = self.keymap[key]
                if not self.held[lane]:  # New press
                    self.held[lane] = True
                    pressed.append(lane)
                    
        # Check for releases (simple timeout-based for now)
        # In a real implementation, you'd want proper key up/down detection
        for lane in range(1, self.keys + 1):
            if self.held[lane]:
                # Auto-release after short time if no re-press (simulated key up)
                # This is a workaround since curses doesn't have proper key up events
                pass
                
        return pressed, released
    
    def release_lane(self, lane: int) -> bool:
        """Manually release a lane (for hold notes)"""
        if self.held[lane]:
            self.held[lane] = False
            return True
        return False

# ---------- .osu parsing ----------
def parse_osu_file(path: str):
    sections = {}
    current = None
    with open(path, encoding="utf-8") as f:
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
    return sections

def parse_hitobjects(hitobject_lines: List[str], keys: int) -> List[Note]:
    notes = []
    lane_width = 512 / keys
    
    for line in hitobject_lines:
        parts = line.split(",")
        if len(parts) < 5:
            continue
            
        x = int(parts[0])
        t = int(parts[2])
        obj_type = int(parts[3])
        
        # Calculate lane (1-indexed)
        lane = int(x / lane_width) + 1
        lane = max(1, min(keys, lane))

        if obj_type & 128:  # Long note (hold)
            extras = parts[5].split(":")
            end_time = int(extras[0])
            notes.append(Note(time=t, lane=lane, kind="hold", end_time=end_time))
        else:  # Normal note
            notes.append(Note(time=t, lane=lane, kind="normal"))
    
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
    """Get score value for judgment"""
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
                # Add color based on approach
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
        if current_time - effect.time_created > effect.duration:
            effects.remove(effect)
            continue
            
        # Animate effect
        progress = (current_time - effect.time_created) / effect.duration
        char_idx = min(len(HIT_EFFECT_CHARS) - 1, int(progress * len(HIT_EFFECT_CHARS)))
        char = HIT_EFFECT_CHARS[char_idx]
        
        lane_x = start_x + (effect.lane - 1) * (LANE_WIDTH + 1)
        
        try:
            # Color based on judgment
            attr = curses.A_BOLD
            if effect.judgment == Judgment.PERFECT:
                attr |= curses.A_REVERSE
            elif effect.judgment == Judgment.MISS:
                attr |= curses.A_DIM
                
            stdscr.addstr(hit_y - 1, lane_x + LANE_WIDTH//2, char, attr)
        except curses.error:
            pass

def draw_frame(stdscr, keys: int, notes: List[Note], current_time: int, stats: Stats, 
              held: Dict[int, bool], effects: List[HitEffect]):
    """Draw the main game frame"""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    play_height = h - 6
    start_x = max(0, (w - (keys * LANE_WIDTH + (keys - 1))) // 2)
    hit_line_y = play_height - 3

    # Draw lanes and highlighting for held keys
    for lane in range(1, keys + 1):
        lane_x = start_x + (lane - 1) * (LANE_WIDTH + 1)
        
        # Lane divider
        if lane > 1:
            for yy in range(play_height):
                try:
                    stdscr.addstr(yy, lane_x - 1, LANE_DIVIDER)
                except curses.error:
                    pass
        
        # Highlight held lanes
        if held.get(lane, False):
            for yy in range(play_height):
                try:
                    stdscr.addstr(yy, lane_x, " " * LANE_WIDTH, curses.A_REVERSE)
                except curses.error:
                    pass

    # Draw notes
    for note in notes:
        if note.judged and note.kind == "normal":
            continue
        if note.kind == "hold" and note.judged and note.hold_judged:
            continue
            
        y = get_note_y_position(note.time, current_time, hit_line_y)
        lane_x = start_x + (note.lane - 1) * (LANE_WIDTH + 1)
        
        # Only draw notes that are visible
        if -10 <= y <= play_height + 10:
            draw_note(stdscr, note, lane_x, y, current_time, play_height, hit_line_y)

    # Draw hit line
    try:
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
        
        judgment_line = f"Perfect: {stats.perfect} | Great: {stats.great} | Good: {stats.good} | Bad: {stats.bad} | Miss: {stats.miss}"
        if len(judgment_line) < w - 1:
            stdscr.addstr(play_height + 3, 0, judgment_line)
        
        # Controls - show actual keys
        key_chars = [chr(k) if k != ord(' ') else 'SPC' for k in DEFAULT_BINDS.get(keys, [ord('d'), ord('f'), ord('j'), ord('k')])]
        controls = f"Keys: {' '.join(key_chars)} | Q: Quit | +/-: Speed"
        stdscr.addstr(play_height + 4, 0, controls)
    except curses.error:
        pass
    
    stdscr.refresh()

# ---------- Game Logic ----------
def handle_note_hit(note: Note, current_time: int, stats: Stats, effects: List[HitEffect]) -> bool:
    """Handle hitting a note head"""
    if note.judged:
        return False
        
    delta = current_time - note.time
    judgment = calculate_judgment(delta)
    
    note.judged = True
    note.hit = judgment != Judgment.MISS
    
    if note.kind == "hold" and judgment != Judgment.MISS:
        note.hold_active = True
    
    update_stats(stats, judgment)
    
    # Add hit effect
    effects.append(HitEffect(note.lane, current_time, judgment))
    
    return judgment != Judgment.MISS

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

def try_load_audio(audio_file: str, beatmap_dir: str) -> bool:
    """Try to load audio with pygame, fallback gracefully"""
    try:
        import pygame.mixer as mixer
        mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        
        song_file = os.path.join(beatmap_dir, audio_file)
        if os.path.exists(song_file):
            mixer.music.load(song_file)
            return True
    except (ImportError, Exception):
        pass
    return False

def run_game(stdscr):
    """Main game loop"""
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
    except FileNotFoundError:
        stdscr.addstr(0, 0, f"Error: Could not find beatmap file:")
        stdscr.addstr(1, 0, f"{OSU_FILE}")
        stdscr.addstr(2, 0, "Press any key to exit...")
        stdscr.getch()
        return
    except Exception as e:
        stdscr.addstr(0, 0, f"Error parsing beatmap: {e}")
        stdscr.addstr(1, 0, "Press any key to exit...")
        stdscr.getch()
        return

    # Get key count
    keys = 4  # Default to 4K
    if "Difficulty" in sections:
        for line in sections["Difficulty"]:
            if line.startswith("CircleSize"):
                keys = int(float(line.split(":")[1]))
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

    # Set up input handler
    input_handler = InputHandler(stdscr, keys)

    # Try to load audio
    audio_loaded = False
    audio_file = None
    if "General" in sections:
        for line in sections["General"]:
            if line.startswith("AudioFilename"):
                audio_file = line.split(":", 1)[1].strip()
                break

    if audio_file:
        audio_loaded = try_load_audio(audio_file, os.path.dirname(OSU_FILE))

    # Game state
    stats = Stats()
    effects = []
    game_started = False
    scroll_speed_multiplier = 1.0
    last_frame_time = time.time()

    # Show ready screen
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, f"osu!mania {keys}K Enhanced")
        key_display = [chr(k) if k != ord(' ') else 'SPACE' for k in DEFAULT_BINDS.get(keys, [ord('d'), ord('f'), ord('j'), ord('k')])]
        stdscr.addstr(1, 0, f"Keys: {' | '.join(key_display)}")
        stdscr.addstr(2, 0, f"Notes: {len(notes)}")
        if audio_loaded:
            stdscr.addstr(3, 0, f"Audio: {audio_file}")
        else:
            stdscr.addstr(3, 0, "Audio: Not loaded (pygame not available)")
        stdscr.addstr(5, 0, "Press SPACE to start")
        stdscr.addstr(6, 0, "Press Q to quit")
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
        try:
            import pygame.mixer as mixer
            mixer.music.play()
        except:
            pass
    
    game_started = True
    running = True

    # Key state tracking for proper hold note handling
    key_press_times = {}
    
    while running:
        current_frame_time = time.time()
        frame_delta = current_frame_time - last_frame_time
        last_frame_time = current_frame_time
        
        current_time = int(time.time() * 1000 - start_time) + AUDIO_OFFSET_MS

        # Handle input
        while True:
            key = stdscr.getch()
            if key == -1:  # No more input
                break
                
            if key == ord('q') or key == ord('Q'):
                running = False
                break
            elif key == ord('+') or key == ord('='):
                scroll_speed_multiplier = min(2.0, scroll_speed_multiplier + 0.1)
                global SCROLL_MS_PER_ROW
                SCROLL_MS_PER_ROW = max(1, int(12 / scroll_speed_multiplier))
            elif key == ord('-'):
                scroll_speed_multiplier = max(0.5, scroll_speed_multiplier - 0.1)
                SCROLL_MS_PER_ROW = max(1, int(12 / scroll_speed_multiplier))
            elif key in input_handler.keymap:
                lane = input_handler.keymap[key]
                
                # Handle key press
                if lane not in key_press_times:  # New press
                    key_press_times[lane] = current_time
                    input_handler.held[lane] = True
                    
                    # Find hittable note in this lane
                    for note in notes:
                        if (note.lane == lane and not note.judged and 
                            abs(current_time - note.time) <= HIT_WINDOW_MISS):
                            handle_note_hit(note, current_time, stats, effects)
                            break
                else:
                    # Key is being held - update hold time
                    key_press_times[lane] = current_time

        # Handle key releases (detect when keys are no longer being pressed)
        current_held = set(key_press_times.keys())
        for lane in list(key_press_times.keys()):
            # If key hasn't been pressed recently, consider it released
            if current_time - key_press_times[lane] > 50:  # 50ms timeout
                del key_press_times[lane]
                input_handler.held[lane] = False
                
                # Handle hold note releases
                for note in notes:
                    if (note.lane == lane and note.kind == "hold" and 
                        note.hold_active and not note.hold_judged):
                        handle_hold_release(note, current_time, stats, effects)

        # Check for missed notes
        check_missed_notes(notes, current_time, stats, effects)

        # Render frame
        draw_frame(stdscr, keys, notes, current_time, stats, input_handler.held, effects)

        # Check if song is finished
        if audio_loaded:
            try:
                import pygame.mixer as mixer
                if not mixer.music.get_busy():
                    # Check if all notes are judged
                    all_judged = all(note.judged and (note.kind != "hold" or note.hold_judged) 
                                   for note in notes)
                    if all_judged:
                        break
            except:
                pass
        else:
            # Without audio, end when all notes are judged
            all_judged = all(note.judged and (note.kind != "hold" or note.hold_judged) 
                           for note in notes)
            if all_judged:
                break

        # Maintain 60 FPS
        time.sleep(max(0, 1/60 - (time.time() - current_frame_time)))

    # Game over screen
    show_results(stdscr, stats, keys)

def show_results(stdscr, stats: Stats, keys: int):
    """Show final results screen"""
    stdscr.erase()
    try:
        stdscr.addstr(0, 0, "┌─────────────────────────────┐")
        stdscr.addstr(1, 0, "│        Game Complete!       │")
        stdscr.addstr(2, 0, "└─────────────────────────────┘")
        
        stdscr.addstr(4, 0, f"Final Score: {stats.score:,}")
        stdscr.addstr(5, 0, f"Max Combo: {stats.max_combo}")
        stdscr.addstr(6, 0, f"Accuracy: {stats.accuracy:.2f}%")
        
        stdscr.addstr(8, 0, "Judgment Breakdown:")
        stdscr.addstr(9, 0, f"  Perfect: {stats.perfect:4d}")
        stdscr.addstr(10, 0, f"  Great:   {stats.great:4d}")
        stdscr.addstr(11, 0, f"  Good:    {stats.good:4d}")
        stdscr.addstr(12, 0, f"  Bad:     {stats.bad:4d}")
        stdscr.addstr(13, 0, f"  Miss:    {stats.miss:4d}")
        
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
            
        stdscr.addstr(15, 0, f"Grade: {grade}", curses.A_BOLD)
        stdscr.addstr(17, 0, "Press any key to exit...")
        stdscr.refresh()
        stdscr.getch()
    except curses.error:
        pass

# -------- Entry --------
def main():
    """Main entry point with better error handling"""
    if not os.path.exists(OSU_FILE):
        print(f"Error: Beatmap file not found: {OSU_FILE}")
        print("Please update the OSU_FILE path in the script.")
        return
    
    try:
        # Test if we can import pygame (optional)
        try:
            import pygame
            print("Pygame detected - audio support enabled")
        except ImportError:
            print("Pygame not found - running without audio")
            print("Install with: sudo pacman -S python-pygame  # Arch Linux")
        
        curses.wrapper(run_game)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure the .osu file path is correct")
        print("2. Install pygame for audio: sudo pacman -S python-pygame")
        print("3. Make sure your terminal supports UTF-8")

if __name__ == "__main__":
    main()
