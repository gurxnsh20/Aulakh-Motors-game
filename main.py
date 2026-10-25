#!/usr/bin/env python3
"""
=====================================================================
  AULAKH MOTORS SIMULATOR  -  2D top-down RPG / simulation (Pygame)
=====================================================================
Run:      pip install pygame   (2.1+)
          python aulakh_motors_simulator.py

MOBILE: runs as-is on Android (build with buildozer, see buildozer.spec).
        Touch UI = floating joystick (left half), E / Q / pause buttons.
        Test on PC with:  python main.py --touch
Controls: WASD / Arrows  move
          E              interact / advance dialogue
          Q              put down carried scooters (rack / loader)
          ESC            pause        F3  debug overlay

Architecture (top to bottom of this file)
  1. Config & helpers            Cfg, Col, text/panel helpers
  2. AssetManager                swap placeholders for real PNGs (see below)
  3. Camera, Input, Fade
  4. GameMap + MapFactory        Sullar Gharat Agency / Ratolan Village
  5. Entities                    Entity, Player, NPC, Customer, Scooter,
                                 Vehicle (BigTruck, SmallLoader), Storage
  6. Systems                     DialogueBox, FloatingText, Spawners
  7. Missions                    MissionManager, UnloadTruck, LoadLoader
  8. World                       owns maps/entities/systems, update + draw
  9. HUD
 10. GameStateManager + States   Menu, Playing, Pause, MissionAccomplished
 11. Game (main loop)

SWAPPING IN REAL ART
  Every entity has a `sprite_key`. AssetManager.sprite() first looks for
  ./assets/<sprite_key>.png and scales it to the entity's size; if the file
  does not exist it paints the coloured placeholder. Keys in use:
    player, npc_tanu, npc_jass, customer, scooter_brand_new,
    scooter_broken, scooter_repaired, truck_big, loader_small, tool_rack
  (Characters/vehicles should face RIGHT; the engine mirrors them.)
=====================================================================
"""
from __future__ import annotations

import math
import os
import random
import sys
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

import pygame
from pygame import Vector2

# =====================================================================
# 1. CONFIG & HELPERS
# =====================================================================
IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_BOOTLOGO" in os.environ
TOUCH_MODE = IS_ANDROID or "--touch" in sys.argv        # `python main.py --touch` tests touch UI on PC (mouse = finger)
# Phones get a smaller logical view (960x540) so everything looks bigger on screen.
SCREEN_W, SCREEN_H = (960, 540) if TOUCH_MODE else (1280, 720)
BACK_KEYS = {pygame.K_ESCAPE, getattr(pygame, "K_AC_BACK", pygame.K_ESCAPE)}   # Android back button
FPS = 60
TITLE = "Aulakh Motors Simulator"
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


class Cfg:
    PLAYER_SPEED = 235.0
    PLAYER_ACCEL = 14.0
    INTERACT_RADIUS = 70          # px from player centre to target rect edge
    CARRY_CAPACITY = 4
    REPAIR_TIME = 3.0
    REPAIR_FEE = 150
    START_CASH = 500
    START_TOOLS = 6
    BIG_TRUCK_CAPACITY = 20
    LOADER_CAPACITY = 9           # exactly 9, never more
    SCOOTER_BASE_VALUE = 1200
    CUSTOMER_PATIENCE = 40.0
    CUSTOMER_MAX = 3
    CREW_WALK_SPEED = 125
    CREW_LOAD_TIME = 1.0
    CREW_DROP_TIME = 0.7
    MISSION_REWARD_UNLOAD = 1500
    MISSION_REWARD_LOAD = 2500


class Col:
    WHITE = (240, 242, 248)
    BLACK = (10, 10, 14)
    PANEL = (18, 22, 32)
    ACCENT = (255, 196, 0)
    GOOD = (96, 222, 134)
    BAD = (242, 96, 96)
    MUTED = (150, 158, 176)
    SKIN = (236, 190, 150)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def money(n: int) -> str:
    return f"Rs {n:,}"


def rect_distance(rect: pygame.Rect, p: Vector2) -> float:
    """Distance from point to the nearest point of a rect (0 if inside)."""
    dx = max(rect.left - p.x, 0, p.x - rect.right)
    dy = max(rect.top - p.y, 0, p.y - rect.bottom)
    return math.hypot(dx, dy)


def wrap_text(font: pygame.font.Font, text: str, max_w: int) -> List[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if font.size(trial)[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_text(surf, font, text, pos, color=Col.WHITE, anchor="topleft", shadow=True):
    if not text:
        return pygame.Rect(pos, (0, 0))
    img = font.render(text, True, color)
    r = img.get_rect()
    setattr(r, anchor, pos)
    if shadow:
        surf.blit(font.render(text, True, (0, 0, 0)), r.move(1, 1))
    surf.blit(img, r)
    return r


def draw_panel(surf, rect, color=Col.PANEL, alpha=215, border=None, radius=10):
    rect = pygame.Rect(rect)
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (*color, alpha), panel.get_rect(), border_radius=radius)
    if border:
        pygame.draw.rect(panel, border, panel.get_rect(), 2, border_radius=radius)
    surf.blit(panel, rect)


def draw_bar(surf, rect, frac, fg=Col.ACCENT, bg=(40, 44, 58)):
    rect = pygame.Rect(rect)
    pygame.draw.rect(surf, bg, rect, border_radius=4)
    inner = rect.copy()
    inner.width = int(rect.width * clamp(frac, 0, 1))
    if inner.width > 0:
        pygame.draw.rect(surf, fg, inner, border_radius=4)
    pygame.draw.rect(surf, (0, 0, 0), rect, 1, border_radius=4)


# =====================================================================
# 2. ASSET MANAGER
# =====================================================================
class AssetManager:
    """Loads PNGs from ./assets if present, otherwise paints placeholders."""

    def __init__(self):
        self._sprites: Dict[Tuple[str, Tuple[int, int]], pygame.Surface] = {}
        self._fonts: Dict[Tuple[int, bool], pygame.font.Font] = {}
        self._shadows: Dict[Tuple[int, int], pygame.Surface] = {}

    def font(self, size: int, bold: bool = False) -> pygame.font.Font:
        key = (size, bold)
        if key not in self._fonts:
            try:
                self._fonts[key] = pygame.font.SysFont(
                    "segoeui,arial,helvetica,dejavusans,verdana", size, bold=bold)
            except Exception:                      # Android may have no SysFont
                self._fonts[key] = pygame.font.Font(None, int(size * 1.2))
        return self._fonts[key]

    def sprite(self, key: str, size: Tuple[int, int],
               painter: Callable[[pygame.Surface], None]) -> pygame.Surface:
        ck = (key, tuple(size))
        if ck in self._sprites:
            return self._sprites[ck]
        path = os.path.join(ASSET_DIR, key + ".png")
        if os.path.isfile(path):
            img = pygame.image.load(path).convert_alpha()
            img = pygame.transform.smoothscale(img, size)
        else:
            img = pygame.Surface(size, pygame.SRCALPHA)
            painter(img)
        self._sprites[ck] = img
        return img

    def shadow(self, w: int, h: int) -> pygame.Surface:
        if (w, h) not in self._shadows:
            s = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.ellipse(s, (0, 0, 0, 70), s.get_rect())
            self._shadows[(w, h)] = s
        return self._shadows[(w, h)]


# =====================================================================
# 3. CAMERA / INPUT / FADE
# =====================================================================
class Camera:
    def __init__(self, vw: int, vh: int):
        self.view = Vector2(vw, vh)
        self.pos = Vector2()
        self.bounds = pygame.Rect(0, 0, vw, vh)
        self.stiffness = 6.0

    def set_bounds(self, rect: pygame.Rect):
        self.bounds = rect.copy()

    def _clamp(self):
        for axis, lo, size, vsize in ((0, self.bounds.left, self.bounds.w, self.view.x),
                                      (1, self.bounds.top, self.bounds.h, self.view.y)):
            if size <= vsize:
                self.pos[axis] = lo + size / 2 - vsize / 2
            else:
                self.pos[axis] = clamp(self.pos[axis], lo, lo + size - vsize)

    def snap_to(self, target: Vector2):
        self.pos = Vector2(target) - self.view / 2
        self._clamp()

    def update(self, target: Vector2, dt: float):
        desired = Vector2(target) - self.view / 2
        t = 1.0 - math.exp(-self.stiffness * dt)   # frame-rate independent lerp
        self.pos += (desired - self.pos) * t
        self._clamp()

    @property
    def offset(self) -> Tuple[int, int]:
        return round(self.pos.x), round(self.pos.y)

    def apply(self, rect: pygame.Rect) -> pygame.Rect:
        ox, oy = self.offset
        return rect.move(-ox, -oy)

    def apply_pos(self, p) -> Tuple[int, int]:
        ox, oy = self.offset
        return int(p[0] - ox), int(p[1] - oy)


class Input:
    def __init__(self):
        self.just: set = set()
        self.touch_axis = Vector2()

    def begin_frame(self):
        self.just.clear()

    def feed(self, event):
        if event.type == pygame.KEYDOWN:
            self.just.add(event.key)

    def axis(self) -> Vector2:
        k = pygame.key.get_pressed()
        v = Vector2(
            (k[pygame.K_d] or k[pygame.K_RIGHT]) - (k[pygame.K_a] or k[pygame.K_LEFT]),
            (k[pygame.K_s] or k[pygame.K_DOWN]) - (k[pygame.K_w] or k[pygame.K_UP]))
        if v.length_squared() > 0:
            return v.normalize()
        return Vector2(self.touch_axis)             # analog stick (length 0..1)


class TouchControls:
    """Floating joystick on the left half + action buttons on the right."""
    DEAD = 0.18

    def __init__(self, enabled: bool, emulate_mouse: bool):
        self.enabled, self.emulate = enabled, emulate_mouse
        self.home = Vector2(140, SCREEN_H - 120)
        self.radius = 68
        self.stick_id: Optional[int] = None
        self.center, self.knob = Vector2(self.home), Vector2(self.home)
        self.axis = Vector2()
        self.held: Dict[int, str] = {}
        # name: (centre, radius, label, key sent to the game)
        self.buttons = {
            "interact": (Vector2(SCREEN_W - 100, SCREEN_H - 115), 58, "E", pygame.K_e),
            "drop": (Vector2(SCREEN_W - 232, SCREEN_H - 62), 40, "Q", pygame.K_q),
            "pause": (Vector2(SCREEN_W - 44, 150), 28, "II", pygame.K_ESCAPE)}
        self._layer = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)

    def convert(self, ev, view_rect: pygame.Rect):
        """pygame event -> (kind, finger_id, pos in canvas coords) or None."""
        if not self.enabled:
            return None
        if ev.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            ww, wh = pygame.display.get_surface().get_size()
            px, py, fid = ev.x * ww, ev.y * wh, ev.finger_id
            kind = {pygame.FINGERDOWN: "down", pygame.FINGERMOTION: "move", pygame.FINGERUP: "up"}[ev.type]
        elif self.emulate and ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            if ev.type == pygame.MOUSEMOTION:
                if not ev.buttons[0]:
                    return None
                kind = "move"
            else:
                if ev.button != 1:
                    return None
                kind = "down" if ev.type == pygame.MOUSEBUTTONDOWN else "up"
            (px, py), fid = ev.pos, -1
        else:
            return None
        x = (px - view_rect.x) / max(1, view_rect.w) * SCREEN_W
        y = (py - view_rect.y) / max(1, view_rect.h) * SCREEN_H
        return kind, fid, Vector2(x, y)

    def down(self, fid, pos, fire: Callable):
        for name, (c, r, _, key) in self.buttons.items():
            if (pos - c).length() <= r * 1.3:
                self.held[fid] = name
                fire(key)
                return
        if self.stick_id is None and pos.x < SCREEN_W * 0.55:
            self.stick_id = fid
            self.center, self.knob, self.axis = Vector2(pos), Vector2(pos), Vector2()

    def move(self, fid, pos):
        if fid != self.stick_id:
            return
        d = pos - self.center
        if d.length() > self.radius:
            d.scale_to_length(self.radius)
        self.knob = self.center + d
        a = d / self.radius
        self.axis = a if a.length() > self.DEAD else Vector2()

    def up(self, fid):
        self.held.pop(fid, None)
        if fid == self.stick_id:
            self.reset_stick()

    def reset_stick(self):
        self.stick_id = None
        self.axis = Vector2()
        self.center, self.knob = Vector2(self.home), Vector2(self.home)

    def reset(self):
        self.held.clear()
        self.reset_stick()

    def draw(self, surf, assets: AssetManager):
        if not self.enabled:
            return
        L = self._layer
        L.fill((0, 0, 0, 0))
        active = self.stick_id is not None
        pygame.draw.circle(L, (255, 255, 255, 46 if active else 30), self.center, self.radius)
        pygame.draw.circle(L, (255, 255, 255, 110), self.center, self.radius, 3)
        pygame.draw.circle(L, (255, 255, 255, 150 if active else 80), self.knob, 28)
        pressed = set(self.held.values())
        font = assets.font(30, True)
        for name, (c, r, label, _) in self.buttons.items():
            col = (255, 196, 0) if name == "interact" else (255, 255, 255)
            pygame.draw.circle(L, (*col, 120 if name in pressed else 62), c, r)
            pygame.draw.circle(L, (*col, 170), c, r, 3)
            img = font.render(label, True, (255, 255, 255))
            img.set_alpha(220)
            L.blit(img, img.get_rect(center=c))
        surf.blit(L, (0, 0))


class Fade:
    """Fade-out -> callback at black -> fade-in. Used for map travel."""

    def __init__(self, duration: float = 0.28):
        self.dur = duration
        self.t = 0.0
        self.phase: Optional[str] = None
        self.cb: Optional[Callable] = None
        self._veil = pygame.Surface((SCREEN_W, SCREEN_H))
        self._veil.fill((0, 0, 0))

    @property
    def active(self) -> bool:
        return self.phase is not None

    def start(self, cb: Callable):
        if self.phase:
            return
        self.phase, self.t, self.cb = "out", 0.0, cb

    def update(self, dt: float):
        if not self.phase:
            return
        self.t += dt
        if self.t >= self.dur:
            if self.phase == "out":
                if self.cb:
                    self.cb()
                self.phase, self.t = "in", 0.0
            else:
                self.phase = None

    def draw(self, surf):
        if not self.phase:
            return
        f = clamp(self.t / self.dur, 0, 1)
        a = f if self.phase == "out" else 1 - f
        self._veil.set_alpha(int(255 * a))
        surf.blit(self._veil, (0, 0))


# =====================================================================
# 4. MAPS
# =====================================================================
class Zone:
    def __init__(self, name, rect, color, kind, label=""):
        self.name, self.rect, self.color, self.kind, self.label = name, pygame.Rect(rect), color, kind, label


class Trigger:
    def __init__(self, rect, target_map, spawn, label):
        self.rect, self.target_map, self.spawn, self.label = pygame.Rect(rect), target_map, Vector2(spawn), label


class Decor:
    def __init__(self, rect, color, shape="rect", solid=True):
        self.rect, self.color, self.shape, self.solid = pygame.Rect(rect), color, shape, solid


class GameMap:
    def __init__(self, map_id: str, name: str, size: Tuple[int, int], ground):
        self.id, self.name, self.size, self.ground = map_id, name, size, ground
        self.bounds = pygame.Rect(0, 0, *size)
        self.zones: Dict[str, Zone] = {}
        self.walls: List[pygame.Rect] = []
        self.decor: List[Decor] = []
        self.triggers: List[Trigger] = []
        self.anchors: Dict[str, object] = {}
        self.solids: List[pygame.Rect] = []

    def finalize(self):
        self.solids = list(self.walls) + [d.rect for d in self.decor if d.solid]

    def zone_name_at(self, p) -> str:
        road = None
        for z in self.zones.values():
            if z.rect.collidepoint(p):
                if z.kind == "road":
                    road = z
                else:
                    return z.label.title()
        if road:
            return road.label.title() or "Road"
        return ""

    def draw(self, surf, cam: Camera, assets: AssetManager, t: float, debug=False):
        surf.fill((10, 12, 16))
        screen_map = cam.apply(self.bounds)
        pygame.draw.rect(surf, self.ground, screen_map)
        surf.set_clip(screen_map.clip(surf.get_rect()))
        # subtle grid for motion feedback
        grid = tuple(max(0, c - 9) for c in self.ground)
        step = 80
        ox, oy = cam.offset
        for x in range(-(ox % step), SCREEN_W, step):
            pygame.draw.line(surf, grid, (x, 0), (x, SCREEN_H))
        for y in range(-(oy % step), SCREEN_H, step):
            pygame.draw.line(surf, grid, (0, y), (SCREEN_W, y))
        font = assets.font(18, True)
        for z in self.zones.values():
            r = cam.apply(z.rect)
            pygame.draw.rect(surf, z.color, r)
            border = tuple(max(0, c - 25) for c in z.color)
            pygame.draw.rect(surf, border, r, 2)
            if z.kind == "road":
                for x in range(z.rect.left + 20, z.rect.right, 90):
                    dash = cam.apply(pygame.Rect(x, z.rect.centery - 3, 44, 6))
                    pygame.draw.rect(surf, (220, 205, 120), dash, border_radius=2)
            if z.label:
                draw_text(surf, font, z.label, (r.x + 10, r.y + 6), (255, 255, 255), shadow=True)
        for w in self.walls:
            r = cam.apply(w)
            pygame.draw.rect(surf, (74, 64, 66), r)
            pygame.draw.rect(surf, (104, 92, 92), r, 1)
        for d in self.decor:
            r = cam.apply(d.rect)
            if d.shape == "tree":
                pygame.draw.rect(surf, (96, 66, 40), (r.centerx - 5, r.centery - 2, 10, r.h // 2 + 2))
                pygame.draw.circle(surf, (44, 112, 56), (r.centerx, r.centery - 8), int(r.w * 0.95))
                pygame.draw.circle(surf, (62, 140, 72), (r.centerx - 6, r.centery - 14), int(r.w * 0.5))
            else:
                pygame.draw.rect(surf, d.color, r, border_radius=4)
                pygame.draw.rect(surf, tuple(max(0, c - 40) for c in d.color), r, 2, border_radius=4)
        pulse = 0.5 + 0.5 * math.sin(t * 4)
        for tr in self.triggers:
            r = cam.apply(tr.rect)
            col = (int(180 + 75 * pulse), int(150 + 46 * pulse), 0)
            pygame.draw.rect(surf, col, r, 3, border_radius=6)
        if debug:
            for sol in self.solids:
                pygame.draw.rect(surf, (255, 0, 0), cam.apply(sol), 1)
        surf.set_clip(None)
        for tr in self.triggers:                      # labels drawn unclipped, kept inside the map edge
            r = cam.apply(tr.rect)
            f = assets.font(18, True)
            if tr.rect.right >= self.bounds.right:
                draw_text(surf, f, tr.label, (r.right, r.top - 6), Col.ACCENT, "bottomright")
            elif tr.rect.left <= 0:
                draw_text(surf, f, tr.label, (r.left, r.top - 6), Col.ACCENT, "bottomleft")
            else:
                draw_text(surf, f, tr.label, (r.centerx, r.top - 6), Col.ACCENT, "midbottom")


def _hseg(x0, x1, y, th, gap):
    if gap is None:
        return [pygame.Rect(x0, y, x1 - x0, th)]
    return [pygame.Rect(x0, y, gap[0] - x0, th), pygame.Rect(gap[1], y, x1 - gap[1], th)]


def _vseg(y0, y1, x, th, gap):
    if gap is None:
        return [pygame.Rect(x, y0, th, y1 - y0)]
    return [pygame.Rect(x, y0, th, gap[0] - y0), pygame.Rect(x, gap[1], th, y1 - gap[1])]


def room_walls(rect: pygame.Rect, th: int, gaps: Dict[str, Tuple[int, int]]) -> List[pygame.Rect]:
    """Four walls around `rect`; `gaps` = {side: (start, end)} in absolute coords."""
    w: List[pygame.Rect] = []
    w += _hseg(rect.left, rect.right, rect.top, th, gaps.get("top"))
    w += _hseg(rect.left, rect.right, rect.bottom - th, th, gaps.get("bottom"))
    w += _vseg(rect.top, rect.bottom, rect.left, th, gaps.get("left"))
    w += _vseg(rect.top, rect.bottom, rect.right - th, th, gaps.get("right"))
    return w


class MapFactory:
    @staticmethod
    def build_all() -> Dict[str, GameMap]:
        maps = {"sullar": MapFactory.sullar_gharat(), "ratolan": MapFactory.ratolan()}
        for m in maps.values():
            m.finalize()
        return maps

    @staticmethod
    def sullar_gharat() -> GameMap:
        m = GameMap("sullar", "Sullar Gharat Agency", (1600, 1000), (92, 100, 88))
        m.zones["road"] = Zone("road", (0, 860, 1600, 140), (58, 58, 66), "road", "")
        show = pygame.Rect(60, 60, 640, 380)
        m.zones["showroom"] = Zone("showroom", show.inflate(-16, -16), (200, 190, 170), "showroom", "SHOWROOM")
        m.walls += room_walls(show, 16, {"bottom": (300, 420)})
        bay = pygame.Rect(760, 60, 520, 380)
        m.zones["repair_bay"] = Zone("repair_bay", bay.inflate(-16, -16), (122, 128, 140), "repair", "REPAIR BAY")
        m.walls += room_walls(bay, 16, {"bottom": (900, 1060)})
        m.zones["unload"] = Zone("unload", (100, 540, 820, 300), (106, 114, 102), "unload", "UNLOADING ZONE")
        # furniture
        m.decor.append(Decor((100, 100, 200, 36), (120, 84, 56)))          # sales counter
        m.decor.append(Decor((800, 100, 400, 30), (90, 96, 110)))           # workbench
        m.decor.append(Decor((1000, 600, 90, 60), (150, 150, 160)))         # parts crates
        m.decor.append(Decor((1130, 620, 80, 60), (150, 150, 160)))
        # anchors
        m.anchors["spawn_default"] = Vector2(700, 520)
        m.anchors["truck_park"] = Vector2(560, 915)
        m.anchors["truck_entry"] = Vector2(-320, 915)
        m.anchors["truck_exit"] = Vector2(1920, 915)
        m.anchors["unload_route"] = [Vector2(560, 838), Vector2(360, 500), Vector2(360, 380), Vector2(200, 305)]
        m.anchors["repair_slots"] = [Vector2(870, 270), Vector2(1020, 270), Vector2(1170, 270)]
        m.anchors["customer_spawn"] = Vector2(360, 975)
        m.anchors["customer_door"] = [Vector2(360, 470), Vector2(360, 395)]
        m.anchors["customer_slots"] = [Vector2(470, 290), Vector2(560, 290), Vector2(650, 290)]
        m.anchors["crew"] = {"Tanu": (Vector2(300, 260), pygame.Rect(130, 190, 520, 190)),
                             "Jass": (Vector2(400, 650), pygame.Rect(140, 590, 700, 200))}
        m.triggers.append(Trigger((1520, 520, 80, 300), "ratolan", (150, 690), "To Ratolan Village ->"))
        return m

    @staticmethod
    def ratolan() -> GameMap:
        m = GameMap("ratolan", "Ratolan Village", (1800, 1000), (124, 142, 94))
        m.zones["road"] = Zone("road", (0, 600, 1800, 180), (152, 122, 86), "road", "DIRT ROAD")
        shed = pygame.Rect(60, 60, 720, 400)
        m.zones["shed"] = Zone("shed", shed.inflate(-16, -16), (150, 134, 110), "shed", "LARGE SHED")
        m.walls += room_walls(shed, 16, {"bottom": (300, 460)})
        house = pygame.Rect(1000, 80, 420, 320)
        m.zones["house"] = Zone("house", house.inflate(-16, -16), (178, 152, 140), "house", "HOUSE")
        m.walls += room_walls(house, 16, {"bottom": (1150, 1250)})
        # furniture + trees
        m.decor.append(Decor((1040, 120, 120, 60), (110, 80, 70)))          # bed
        m.decor.append(Decor((1260, 120, 100, 70), (130, 100, 80)))         # table
        for tx, ty in [(880, 180), (880, 400), (200, 880), (620, 870), (1050, 880),
                       (1500, 860), (1600, 300), (1650, 520), (1450, 450)]:
            m.decor.append(Decor((tx - 14, ty - 14, 28, 28), (0, 0, 0), "tree", True))
        m.anchors["spawn_default"] = Vector2(150, 690)
        m.anchors["truck_park"] = Vector2(400, 690)
        m.anchors["truck_entry"] = Vector2(2120, 690)
        m.anchors["truck_exit"] = Vector2(-420, 690)
        m.anchors["unload_route"] = [Vector2(400, 624), Vector2(380, 520), Vector2(380, 410), Vector2(400, 222)]
        m.anchors["crew"] = {"Tanu": (Vector2(320, 300), pygame.Rect(110, 200, 620, 200)),
                             "Jass": (Vector2(500, 300), pygame.Rect(110, 200, 620, 200))}
        m.triggers.append(Trigger((0, 610, 70, 160), "sullar", (1440, 670), "<- To Sullar Gharat"))
        return m


# =====================================================================
# 5. ENTITIES
# =====================================================================
class ScooterState(Enum):
    BRAND_NEW = "Brand New"
    BROKEN = "Broken"
    REPAIRED = "Repaired"


class Location(Enum):
    TRUCK = auto()
    SHED = auto()
    SHOWROOM = auto()
    REPAIR_BAY = auto()
    CARRIED = auto()
    LOADER = auto()
    SOLD = auto()
    DEPARTED = auto()


class Interactable:
    """Mixin: anything the player can press E (or Q) on."""

    def can_interact(self, player, world) -> bool:
        return True

    def prompt(self, player, world) -> str:
        return "[E] Interact"

    def interact(self, player, world):
        pass

    def accepts_deposit(self, player, world) -> bool:
        return False

    def deposit(self, player, world):
        pass


class PathWalker:
    """Mixin: waypoint following with stuck recovery."""

    def init_walker(self):
        self.path: List[Vector2] = []
        self._stuck = 0.0

    def set_path(self, pts):
        self.path = [Vector2(p) for p in pts]
        self._stuck = 0.0

    def follow(self, dt, world, speed) -> bool:
        """Advance along path; True once the path is finished."""
        if not self.path:
            return True
        before = Vector2(self.pos)
        if self.walk_towards(self.path[0], speed, dt, world):
            self.path.pop(0)
            self._stuck = 0.0
            return not self.path
        if (self.pos - before).length() < speed * dt * 0.25:
            self._stuck += dt
        else:
            self._stuck = 0.0
        if self._stuck > 0.8:                      # wedged on geometry: snap to waypoint
            self.pos = Vector2(self.path[0])
            self.rect.center = (round(self.pos.x), round(self.pos.y))
            self._stuck = 0.0
        return False


class Entity:
    flip_on_face = False
    has_shadow = True

    def __init__(self, pos, size, color, sprite_key="entity"):
        self.pos = Vector2(pos)
        self.rect = pygame.Rect(0, 0, *size)
        self.rect.center = (round(self.pos.x), round(self.pos.y))
        self.color = color
        self.sprite_key = sprite_key
        self.map_id: Optional[str] = None
        self.alive = True
        self.facing = 1
        self.anim_t = random.random() * 10

    # --- hooks ---------------------------------------------------------
    def update(self, dt: float, world: "World"):
        pass

    def paint(self, surf: pygame.Surface):
        pygame.draw.rect(surf, self.color, surf.get_rect(), border_radius=4)

    def bob(self) -> int:
        return 0

    # --- movement ------------------------------------------------------
    def sync_rect(self):
        self.rect.center = (round(self.pos.x), round(self.pos.y))

    def move_and_collide(self, delta: Vector2, solids, bounds: Optional[pygame.Rect] = None):
        """Axis-separated AABB movement with wall sliding."""
        for axis in (0, 1):
            self.pos[axis] += delta[axis]
            self.sync_rect()
            for s in solids:
                if self.rect.colliderect(s):
                    if axis == 0:
                        if delta.x > 0:
                            self.rect.right = s.left
                        elif delta.x < 0:
                            self.rect.left = s.right
                        self.pos.x = self.rect.centerx
                    else:
                        if delta.y > 0:
                            self.rect.bottom = s.top
                        elif delta.y < 0:
                            self.rect.top = s.bottom
                        self.pos.y = self.rect.centery
            if bounds is not None:
                if axis == 0:
                    self.pos.x = clamp(self.pos.x, bounds.left + self.rect.w / 2, bounds.right - self.rect.w / 2)
                else:
                    self.pos.y = clamp(self.pos.y, bounds.top + self.rect.h / 2, bounds.bottom - self.rect.h / 2)
                self.sync_rect()

    def walk_towards(self, target, speed, dt, world, arrive=6.0) -> bool:
        t = Vector2(target)
        d = t - self.pos
        dist = d.length()
        if dist <= arrive:
            return True
        delta = d.normalize() * min(speed * dt, dist)
        gm = world.maps[self.map_id]
        self.move_and_collide(delta, gm.solids, gm.bounds)
        if abs(delta.x) > 0.05:
            self.facing = 1 if delta.x > 0 else -1
        return False

    # --- drawing -------------------------------------------------------
    def sprite_name(self) -> str:
        return self.sprite_key

    def draw(self, surf, cam: Camera, assets: AssetManager, world: "World"):
        r = cam.apply(self.rect)
        if self.has_shadow:
            sh = assets.shadow(int(self.rect.w * 0.9), max(6, int(self.rect.h * 0.35)))
            surf.blit(sh, sh.get_rect(midbottom=(r.centerx, r.bottom + 3)))
        spr = assets.sprite(self.sprite_name(), self.rect.size, self.paint)
        if self.flip_on_face and self.facing < 0:
            spr = pygame.transform.flip(spr, True, False)
        surf.blit(spr, r.move(0, self.bob()))

    def label(self, surf, cam, assets, text, color=Col.WHITE, dy=-6, size=16):
        r = cam.apply(self.rect)
        draw_text(surf, assets.font(size, True), text, (r.centerx, r.top + dy), color, "midbottom")


# --------------------------------------------------------------------- Scooter
class Scooter(Entity, Interactable):
    """'Yakuza Electric Scooter'. Plain data when stocked; an Entity in the repair bay."""
    _next_id = 1
    VALUE_MULT = {ScooterState.BRAND_NEW: 1.0, ScooterState.BROKEN: 0.25, ScooterState.REPAIRED: 0.7}
    STATE_COLOR = {ScooterState.BRAND_NEW: (255, 214, 40), ScooterState.BROKEN: (170, 90, 70),
                   ScooterState.REPAIRED: (90, 200, 255)}

    def __init__(self, state=ScooterState.BRAND_NEW, location=Location.SHOWROOM,
                 base_value=Cfg.SCOOTER_BASE_VALUE, pos=(0, 0)):
        super().__init__(pos, (40, 24), self.STATE_COLOR[state])
        self.id = Scooter._next_id
        Scooter._next_id += 1
        self.state = state
        self.location = location
        self.base_value = base_value
        self.tag: Optional[str] = None       # lets missions track their own cargo
        self.slot = -1
        self.being_repaired = False

    @property
    def value(self) -> int:
        return int(self.base_value * self.VALUE_MULT[self.state])

    def set_state(self, state: ScooterState):
        self.state = state
        self.color = self.STATE_COLOR[state]

    def sprite_name(self):
        return "scooter_" + self.state.name.lower()

    def paint(self, surf):
        w, h = surf.get_size()
        pygame.draw.rect(surf, self.color, (6, 4, w - 12, h - 12), border_radius=6)
        pygame.draw.rect(surf, (30, 30, 36), (w - 14, 0, 6, h - 8), border_radius=2)  # handlebar
        pygame.draw.circle(surf, (24, 24, 28), (9, h - 6), 6)
        pygame.draw.circle(surf, (24, 24, 28), (w - 9, h - 6), 6)
        if self.state == ScooterState.BROKEN:
            pygame.draw.line(surf, (230, 60, 60), (12, 6), (w - 14, h - 10), 3)
            pygame.draw.line(surf, (230, 60, 60), (12, h - 10), (w - 14, 6), 3)

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        r = cam.apply(self.rect)
        if self.state == ScooterState.BROKEN and not self.being_repaired:
            for i in range(3):                       # smoke puffs
                ph = (world.time * 0.9 + i / 3) % 1.0
                pygame.draw.circle(surf, (120 + int(60 * ph),) * 3,
                                   (r.centerx - 6 + i * 6, int(r.top - 4 - ph * 22)), int(3 + ph * 4), 1)
        if self.state == ScooterState.BROKEN:
            self.label(surf, cam, assets, "BROKEN", Col.BAD, -26, 14)

    # Interactable (repair bay)
    def can_interact(self, player, world):
        return (self.state == ScooterState.BROKEN and self.location == Location.REPAIR_BAY
                and not self.being_repaired and player.action is None)

    def prompt(self, player, world):
        if player.inventory.tools <= 0:
            return "No tools left - use the Tool Rack"
        return f"[E] Repair scooter ({Cfg.REPAIR_TIME:.0f}s)"

    def interact(self, player, world):
        if player.inventory.tools <= 0:
            world.popup("Out of tools!", player.pos + Vector2(0, -30), Col.BAD)
            return
        self.being_repaired = True
        player.action = TimedAction("Repairing...", Cfg.REPAIR_TIME,
                                    lambda: self._finish_repair(player, world))

    def _finish_repair(self, player, world):
        self.being_repaired = False
        self.set_state(ScooterState.REPAIRED)
        player.inventory.tools -= 1
        player.inventory.cash += Cfg.REPAIR_FEE
        world.stats["repaired"] += 1
        world.storages["showroom"].add(self)
        self.alive = False
        world.popup(f"Repaired!  +{money(Cfg.REPAIR_FEE)}", player.pos + Vector2(0, -40), Col.GOOD)


# --------------------------------------------------------------------- Storage
class Storage(Entity, Interactable):
    """A rack that holds scooters (showroom floor, shed rack)."""
    has_shadow = False

    def __init__(self, pos, size, label, location: Location, color=(96, 104, 120)):
        super().__init__(pos, size, color, "storage")
        self.title = label
        self.location = location
        self.scooters: List[Scooter] = []

    def add(self, s: Scooter):
        s.location = self.location
        self.scooters.append(s)

    def count_tag(self, tag: str) -> int:
        return sum(1 for s in self.scooters if s.tag == tag)

    def draw(self, surf, cam, assets, world):
        r = cam.apply(self.rect)
        pygame.draw.rect(surf, self.color, r, border_radius=6)
        pygame.draw.rect(surf, (30, 32, 40), r, 2, border_radius=6)
        for i, s in enumerate(self.scooters[:18]):
            x, y = r.x + 10 + (i % 9) * 20, r.y + 14 + (i // 9) * 22
            pygame.draw.rect(surf, Scooter.STATE_COLOR[s.state], (x, y, 16, 10), border_radius=3)
            pygame.draw.rect(surf, (20, 20, 24), (x, y, 16, 10), 1, border_radius=3)
        draw_text(surf, assets.font(18, True), f"{self.title}: {len(self.scooters)}",
                  (r.centerx, r.top - 4), Col.WHITE, "midbottom")

    def can_interact(self, player, world):
        return len(self.scooters) > 0 and player.inventory.free_slots > 0

    def prompt(self, player, world):
        return f"[E] Take scooters from {self.title}"

    def interact(self, player, world):
        n = min(player.inventory.free_slots, len(self.scooters))
        for _ in range(n):
            s = self.scooters.pop()
            s.location = Location.CARRIED
            player.inventory.scooters.append(s)
        world.popup(f"Picked up {n}", player.pos + Vector2(0, -30), Col.ACCENT)

    def accepts_deposit(self, player, world):
        return bool(player.inventory.scooters)

    def deposit(self, player, world):
        n = len(player.inventory.scooters)
        for s in player.inventory.scooters:
            self.add(s)
        player.inventory.scooters.clear()
        world.popup(f"Stored {n}", player.pos + Vector2(0, -30), Col.GOOD)


class ToolRack(Entity, Interactable):
    has_shadow = False

    def __init__(self, pos):
        super().__init__(pos, (64, 70), (150, 110, 60), "tool_rack")

    def paint(self, surf):
        pygame.draw.rect(surf, self.color, surf.get_rect(), border_radius=5)
        for i in range(4):
            pygame.draw.rect(surf, (200, 200, 210), (8 + i * 14, 8, 6, 40), border_radius=2)
        pygame.draw.rect(surf, (30, 30, 36), surf.get_rect(), 2, border_radius=5)

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        self.label(surf, cam, assets, "TOOL RACK", Col.WHITE, -4, 15)

    def can_interact(self, player, world):
        return player.inventory.tools < Cfg.START_TOOLS

    def prompt(self, player, world):
        return "[E] Restock tools"

    def interact(self, player, world):
        player.inventory.tools = Cfg.START_TOOLS
        world.popup("Tools restocked", player.pos + Vector2(0, -30), Col.GOOD)


# --------------------------------------------------------------------- Player
class Inventory:
    def __init__(self):
        self.cash = Cfg.START_CASH
        self.tools = Cfg.START_TOOLS
        self.scooters: List[Scooter] = []
        self.capacity = Cfg.CARRY_CAPACITY

    @property
    def free_slots(self) -> int:
        return self.capacity - len(self.scooters)


class TimedAction:
    def __init__(self, label: str, duration: float, on_complete: Callable):
        self.label, self.duration, self.on_complete = label, duration, on_complete
        self.elapsed = 0.0

    @property
    def progress(self) -> float:
        return clamp(self.elapsed / self.duration, 0, 1)

    def update(self, dt) -> bool:
        self.elapsed += dt
        return self.elapsed >= self.duration


def paint_character(surf, body, accent=(255, 255, 255)):
    w, h = surf.get_size()
    pygame.draw.ellipse(surf, tuple(max(0, c - 40) for c in body), (1, h // 3, w - 2, h * 2 // 3))
    pygame.draw.rect(surf, body, (3, h // 3, w - 6, h // 2), border_radius=7)
    pygame.draw.rect(surf, accent, (3, h // 3 + 3, w - 6, 4))
    pygame.draw.circle(surf, Col.SKIN, (w // 2, h // 3), w // 3)
    pygame.draw.circle(surf, (20, 20, 24), (w // 2 + 4, h // 3 - 1), 2)     # eye (faces right)
    pygame.draw.circle(surf, (20, 20, 24), (w // 2 - 1, h // 3 - 1), 2)


class Player(Entity):
    flip_on_face = True

    def __init__(self, pos):
        super().__init__(pos, (26, 26), (60, 130, 224), "player")
        self.vel = Vector2()
        self.inventory = Inventory()
        self.action: Optional[TimedAction] = None
        self.name = "Jashan"

    def paint(self, surf):
        paint_character(surf, self.color, (255, 196, 0))

    def bob(self):
        return -int(abs(math.sin(self.anim_t * 11)) * 3) if self.vel.length() > 30 else int(math.sin(self.anim_t * 2.5))

    def update(self, dt, world):
        self.anim_t += dt
        axis = Vector2()
        if not world.input_locked and self.action is None:
            axis = world.game.input.axis()
        target = axis * Cfg.PLAYER_SPEED
        self.vel += (target - self.vel) * (1 - math.exp(-Cfg.PLAYER_ACCEL * dt))
        if axis.length_squared() == 0 and self.vel.length() < 4:
            self.vel.update(0, 0)
        if self.vel.length_squared() > 0:
            gm = world.current_map
            self.move_and_collide(self.vel * dt, gm.solids, gm.bounds)
            if abs(self.vel.x) > 20:
                self.facing = 1 if self.vel.x > 0 else -1
        if self.action and self.action.update(dt):
            done, self.action = self.action, None
            done.on_complete()

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        r = cam.apply(self.rect)
        self.label(surf, cam, assets, self.name, (140, 200, 255), -26 if self.inventory.scooters else -6)
        n = len(self.inventory.scooters)
        if n:                                          # carried scooters stacked above head
            for i in range(n):
                pygame.draw.rect(surf, Col.ACCENT, (r.centerx - 22 + i * 12, r.top - 20, 10, 8), border_radius=2)
        if self.action:
            bar = pygame.Rect(0, 0, 70, 10)
            bar.midbottom = (r.centerx, r.top - 30)
            draw_bar(surf, bar, self.action.progress, Col.GOOD)
            draw_text(surf, assets.font(15, True), self.action.label, (bar.centerx, bar.top - 2), Col.WHITE, "midbottom")


# --------------------------------------------------------------------- NPC (Tanu & Jass)
class NPCState(Enum):
    IDLE = auto()
    WANDER = auto()
    TALK = auto()
    TO_TRUCK = auto()
    LOADING = auto()
    TO_DROP = auto()
    UNLOADING = auto()


WORK_STATES = {NPCState.TO_TRUCK, NPCState.LOADING, NPCState.TO_DROP, NPCState.UNLOADING}


class NPC(Entity, Interactable, PathWalker):
    flip_on_face = True

    def __init__(self, name, pos, color, sprite_key, chatter: List[List[str]]):
        super().__init__(pos, (26, 26), color, sprite_key)
        self.init_walker()
        self.name = name
        self.chatter = chatter
        self._chat_i = 0
        self.state = NPCState.IDLE
        self._resume: Optional[NPCState] = None
        self.timer = random.uniform(1, 3)
        self.wander_rect = pygame.Rect(pos[0] - 80, pos[1] - 60, 160, 120)
        self.carrying: Optional[Scooter] = None

    def paint(self, surf):
        paint_character(surf, self.color, (255, 255, 255))

    def bob(self):
        if self.state in (NPCState.WANDER, NPCState.TO_TRUCK, NPCState.TO_DROP):
            return -int(abs(math.sin(self.anim_t * 10)) * 3)
        return int(math.sin(self.anim_t * 2.2) * 1.5)           # idle breathing

    # ---- AI ------------------------------------------------------------
    def update(self, dt, world):
        self.anim_t += dt
        getattr(self, "_st_" + self.state.name.lower())(dt, world)

    def _go_idle(self):
        self.state = NPCState.IDLE
        self.timer = random.uniform(1.5, 3.5)
        self.path = []

    def _st_idle(self, dt, world):
        self.timer -= dt
        if int(self.anim_t * 0.8) % 3 == 0 and random.random() < 0.01:
            self.facing *= -1                                    # look around
        if self.timer <= 0:
            if random.random() < 0.65:
                r = self.wander_rect
                self.set_path([(random.randint(r.left, r.right), random.randint(r.top, r.bottom))])
                self.state = NPCState.WANDER
            else:
                self.timer = random.uniform(1.5, 3.5)

    def _st_wander(self, dt, world):
        if self.follow(dt, world, 55):
            self._go_idle()

    def _st_talk(self, dt, world):
        if not world.dialogue.active:                            # safety net
            self.state = self._resume or NPCState.IDLE
            self._resume = None

    # crew work loop: truck -> carry -> shed/showroom -> repeat
    def assign_unload(self, world):
        job = world.crew_job()
        if not job:
            return
        self.set_path(job.route_to_truck_from(self.pos))
        self.state = NPCState.TO_TRUCK

    def _st_to_truck(self, dt, world):
        job = world.crew_job()
        if not job or not job.truck.cargo:
            return self.finish_task(world)
        if self.follow(dt, world, Cfg.CREW_WALK_SPEED):
            self.state, self.timer = NPCState.LOADING, Cfg.CREW_LOAD_TIME

    def _st_loading(self, dt, world):
        self.timer -= dt
        if self.timer > 0:
            return
        job = world.crew_job()
        s = job.truck.take_one() if job else None
        if s is None:
            return self.finish_task(world)
        s.location = Location.CARRIED
        self.carrying = s
        self.set_path(job.route)
        self.state = NPCState.TO_DROP

    def _st_to_drop(self, dt, world):
        if not world.crew_job():
            return self.finish_task(world)
        if self.follow(dt, world, Cfg.CREW_WALK_SPEED):
            self.state, self.timer = NPCState.UNLOADING, Cfg.CREW_DROP_TIME

    def _st_unloading(self, dt, world):
        self.timer -= dt
        if self.timer > 0:
            return
        job = world.crew_job()
        if job and self.carrying:
            job.storage.add(self.carrying)
            self.carrying = None
            world.popup("+1", self.pos + Vector2(0, -24), Col.GOOD)
        if job and job.truck.cargo:
            self.set_path(job.route_to_truck_from(self.pos))
            self.state = NPCState.TO_TRUCK
        else:
            self.finish_task(world)

    def finish_task(self, world):
        self.reset_task(world)

    def reset_task(self, world):
        """Abort any job; never lose the scooter we were holding."""
        if self.carrying:
            world.stash_scooter(self.carrying, self.map_id)
            self.carrying = None
        self._resume = None
        self._go_idle()

    # ---- Interactable --------------------------------------------------
    def prompt(self, player, world):
        if world.crew_wanted(self):
            return f"[E] Command {self.name}: unload truck"
        return f"[E] Talk to {self.name}"

    def interact(self, player, world):
        self.facing = 1 if player.pos.x > self.pos.x else -1
        cb = None
        if self.state in WORK_STATES:
            lines = ["Kaam chal raha hai, Jashan bhai! Don't distract me."]
            self._resume = self.state
        elif world.crew_wanted(self):
            lines = [random.choice(["Haan bhai, truck khaali karte hain!",
                                    "Done boss, scooters shed mein pahunchate hain!"])]
            cb = lambda: self.assign_unload(world)
        else:
            lines = self.chatter[self._chat_i % len(self.chatter)]
            self._chat_i += 1
        self.state = NPCState.TALK
        world.dialogue.start(self.name, lines, on_close=cb, color=self.color)

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        r = cam.apply(self.rect)
        self.label(surf, cam, assets, self.name, (255, 220, 170), -6 if not self.carrying else -26)
        if self.carrying:
            pygame.draw.rect(surf, Col.ACCENT, (r.centerx - 8, r.top - 18, 16, 9), border_radius=2)
        if self.state == NPCState.IDLE and (self.anim_t % 6) < 1.2:
            draw_text(surf, assets.font(16, True), "...", (r.right + 4, r.top - 2), Col.MUTED)
        if self.state in WORK_STATES:
            draw_text(surf, assets.font(14, True), "working", (r.centerx, r.bottom + 14), Col.GOOD, "midtop")


# --------------------------------------------------------------------- Customer
class Customer(Entity, Interactable, PathWalker):
    flip_on_face = True
    NAMES = ["Harpreet", "Simran", "Gurdeep", "Manjit", "Kuljit", "Navneet", "Balwinder", "Rupinder"]

    def __init__(self, spawn, door_pts, slot_pos, slot_index):
        super().__init__(spawn, (26, 26), (random.randint(90, 220), random.randint(90, 220), random.randint(90, 220)),
                         "customer")
        self.init_walker()
        self.name = random.choice(self.NAMES)
        self.slot = slot_index
        self.spawn = Vector2(spawn)
        self.route = [Vector2(p) for p in door_pts] + [Vector2(slot_pos)]
        self.set_path(self.route)
        self.state = "enter"
        self.patience = Cfg.CUSTOMER_PATIENCE

    def paint(self, surf):
        paint_character(surf, self.color, (60, 60, 70))

    def bob(self):
        return -int(abs(math.sin(self.anim_t * 10)) * 3) if self.state != "wait" else int(math.sin(self.anim_t * 2))

    def update(self, dt, world):
        self.anim_t += dt
        if self.state == "enter":
            if self.follow(dt, world, 95):
                self.state = "wait"
        elif self.state == "wait":
            self.patience -= dt
            if self.patience <= 0:
                world.popup("Customer left", self.pos + Vector2(0, -26), Col.BAD)
                self.leave()
        elif self.state == "leave":
            if self.follow(dt, world, 110):
                self.alive = False

    def leave(self):
        self.state = "leave"
        self.slot = -1
        self.set_path(list(reversed(self.route[:-1])) + [self.spawn])

    def can_interact(self, player, world):
        return self.state == "wait"

    def prompt(self, player, world):
        n = len(world.storages["showroom"].scooters)
        return f"[E] Sell scooter to {self.name}" if n else f"{self.name} wants a scooter - showroom is empty!"

    def interact(self, player, world):
        store = world.storages["showroom"]
        if not store.scooters:
            world.dialogue.start(self.name, ["No scooters in stock? I'll try another agency, then."])
            self.leave()
            return
        s = store.scooters.pop(0)
        s.location = Location.SOLD
        price = int(s.value * random.uniform(1.0, 1.15))
        player.inventory.cash += price
        world.stats["sold"] += 1
        world.popup(f"SOLD!  +{money(price)}", self.pos + Vector2(0, -34), Col.GOOD)
        self.leave()

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        r = cam.apply(self.rect)
        if self.state == "wait":
            bubble = pygame.Rect(0, 0, 48, 20)
            bubble.midbottom = (r.centerx, r.top - 12)
            draw_panel(surf, bubble, (250, 250, 255), 235, (60, 60, 70), 8)
            draw_bar(surf, bubble.inflate(-10, -12), self.patience / Cfg.CUSTOMER_PATIENCE,
                     Col.GOOD if self.patience > 12 else Col.BAD, (210, 210, 220))
            self.label(surf, cam, assets, self.name, (200, 220, 255), -34, 14)


# --------------------------------------------------------------------- Vehicles
class VState(Enum):
    ARRIVING = auto()
    PARKED = auto()
    DEPARTING = auto()
    GONE = auto()


class Vehicle(Entity, Interactable):
    flip_on_face = True

    def __init__(self, title, capacity, size, color, key, entry, park, exit_, speed=250):
        super().__init__(entry, size, color, key)
        self.title = title
        self.capacity = capacity
        self.cargo: List[Scooter] = []
        self.park, self.exit = Vector2(park), Vector2(exit_)
        self.speed = speed
        self.state = VState.ARRIVING
        self.facing = 1 if self.park.x > self.pos.x else -1
        self.on_parked: Optional[Callable] = None
        self.on_departed: Optional[Callable] = None

    def depart(self):
        if self.state == VState.PARKED:
            self.state = VState.DEPARTING

    def update(self, dt, world):
        if self.state in (VState.ARRIVING, VState.DEPARTING):
            target = self.park if self.state == VState.ARRIVING else self.exit
            dx = target.x - self.pos.x
            spd = self.speed
            if self.state == VState.ARRIVING:
                spd = max(50.0, min(self.speed, abs(dx) * 2.0))        # ease into the parking spot
            step = spd * dt
            if abs(dx) <= step:
                self.pos.x = target.x
                if self.state == VState.ARRIVING:
                    self.state = VState.PARKED
                    if self.on_parked:
                        self.on_parked()
                else:
                    self.state = VState.GONE
                    self.alive = False
                    if self.on_departed:
                        self.on_departed()
            else:
                self.pos.x += math.copysign(step, dx)
            self.sync_rect()

    def paint(self, surf):
        w, h = surf.get_size()
        pygame.draw.rect(surf, (226, 228, 234), (0, 4, int(w * 0.68), h - 18), border_radius=6)       # cargo box
        pygame.draw.rect(surf, self.color, (0, 4 + (h - 18) // 2 - 4, int(w * 0.68), 8))              # stripe
        pygame.draw.rect(surf, self.color, (int(w * 0.70), 14, int(w * 0.30), h - 30), border_radius=8)  # cab
        pygame.draw.rect(surf, (170, 220, 250), (int(w * 0.84), 20, int(w * 0.12), h // 4), border_radius=3)
        for cx in (int(w * 0.16), int(w * 0.50), int(w * 0.84)):
            pygame.draw.circle(surf, (24, 24, 28), (cx, h - 10), 9)
            pygame.draw.circle(surf, (120, 120, 130), (cx, h - 10), 4)

    def draw(self, surf, cam, assets, world):
        super().draw(surf, cam, assets, world)
        self.label(surf, cam, assets, f"{self.title}  {len(self.cargo)}/{self.capacity}", Col.WHITE, -6, 18)

    def take_one(self) -> Optional[Scooter]:
        return self.cargo.pop() if self.cargo else None

    def load(self, scooters: List[Scooter]) -> Tuple[List[Scooter], List[Scooter]]:
        """Accept up to remaining capacity; the rest are REJECTED (never exceeds capacity)."""
        space = max(0, self.capacity - len(self.cargo))
        accepted, rejected = scooters[:space], scooters[space:]
        for s in accepted:
            s.location = Location.LOADER
            self.cargo.append(s)
        return accepted, rejected


class BigTruck(Vehicle):
    def __init__(self, entry, park, exit_, tag):
        super().__init__("BIG TRUCK", Cfg.BIG_TRUCK_CAPACITY, (230, 96), (205, 70, 60),
                         "truck_big", entry, park, exit_, 230)
        for _ in range(self.capacity):
            s = Scooter(ScooterState.BRAND_NEW, Location.TRUCK)
            s.tag = tag
            self.cargo.append(s)

    def can_interact(self, player, world):
        return self.state == VState.PARKED and bool(self.cargo) and player.inventory.free_slots > 0

    def prompt(self, player, world):
        return f"[E] Carry scooters off the truck ({len(self.cargo)} left)"

    def interact(self, player, world):
        n = min(player.inventory.free_slots, len(self.cargo))
        for _ in range(n):
            s = self.cargo.pop()
            s.location = Location.CARRIED
            player.inventory.scooters.append(s)
        world.popup(f"Picked up {n} - take them to the rack (Q)", player.pos + Vector2(0, -30), Col.ACCENT)


class SmallLoader(Vehicle):
    """'Choti Gaadi' - holds EXACTLY 9 scooters and rejects anything above that."""

    def __init__(self, entry, park, exit_):
        super().__init__("CHOTI GAADI", Cfg.LOADER_CAPACITY, (150, 80), (60, 140, 205),
                         "loader_small", entry, park, exit_, 230)

    @property
    def full(self) -> bool:
        return len(self.cargo) == self.capacity

    def can_interact(self, player, world):
        return self.state == VState.PARKED and (bool(player.inventory.scooters) or
                                                (bool(self.cargo) and not self.full))

    def prompt(self, player, world):
        if player.inventory.scooters:
            return f"[E] Load {len(player.inventory.scooters)} scooter(s)  ({len(self.cargo)}/{self.capacity})"
        return "[E] Take scooters back out"

    def accepts_deposit(self, player, world):
        return self.state == VState.PARKED and bool(player.inventory.scooters)

    def deposit(self, player, world):
        self.interact(player, world)

    def interact(self, player, world):
        inv = player.inventory
        if inv.scooters:
            accepted, rejected = self.load(inv.scooters)
            inv.scooters = rejected
            if accepted:
                world.popup(f"Loaded {len(accepted)}  ({len(self.cargo)}/{self.capacity})",
                            self.pos + Vector2(0, -60), Col.GOOD)
            if rejected:
                world.popup(f"REJECTED {len(rejected)}: loader holds exactly {self.capacity}!",
                            self.pos + Vector2(0, -90), Col.BAD)
        elif self.cargo and not self.full:
            n = min(inv.free_slots, len(self.cargo))
            for _ in range(n):
                s = self.cargo.pop()
                s.location = Location.CARRIED
                inv.scooters.append(s)
            world.popup(f"Took back {n}", self.pos + Vector2(0, -60), Col.ACCENT)


# =====================================================================
# 6. SYSTEMS: DIALOGUE, FLOATING TEXT, SPAWNERS
# =====================================================================
class DialogueBox:
    CPS = 55.0   # typewriter chars / second

    def __init__(self, assets: AssetManager):
        self.assets = assets
        self.queue: List[str] = []
        self.speaker = ""
        self.color = Col.ACCENT
        self.text = ""
        self.shown = 0.0
        self.on_close: Optional[Callable] = None
        self.active = False

    def start(self, speaker, lines: List[str], on_close: Optional[Callable] = None, color=Col.ACCENT):
        self.speaker, self.queue, self.on_close, self.color = speaker, list(lines), on_close, color
        self.active = True
        self._next()

    def _next(self):
        self.text, self.shown = self.queue.pop(0), 0.0

    def advance(self):
        if not self.active:
            return
        if self.shown < len(self.text):
            self.shown = len(self.text)
        elif self.queue:
            self._next()
        else:
            self.active = False
            cb, self.on_close = self.on_close, None
            if cb:
                cb()

    def update(self, dt):
        if self.active:
            self.shown = min(len(self.text), self.shown + self.CPS * dt)

    def draw(self, surf):
        if not self.active:
            return
        box = pygame.Rect(90, SCREEN_H - 190, SCREEN_W - 180, 150)
        draw_panel(surf, box, (14, 18, 28), 238, self.color, 14)
        tab = pygame.Rect(box.x + 20, box.y - 18, 170, 34)
        draw_panel(surf, tab, self.color, 255, None, 8)
        draw_text(surf, self.assets.font(24, True), self.speaker, tab.center, (20, 20, 24), "center", shadow=False)
        font = self.assets.font(26)
        for i, line in enumerate(wrap_text(font, self.text[:int(self.shown)], box.w - 50)[:4]):
            draw_text(surf, font, line, (box.x + 26, box.y + 26 + i * 30))
        if self.shown >= len(self.text):
            draw_text(surf, self.assets.font(18), "[E] continue", (box.right - 20, box.bottom - 14), Col.MUTED, "bottomright")


class FloatingText:
    def __init__(self, text, pos, color, life=1.4):
        self.text, self.pos, self.color = text, Vector2(pos), color
        self.life = self.max_life = life

    def update(self, dt):
        self.life -= dt
        self.pos.y -= 34 * dt

    def draw(self, surf, cam, assets):
        a = clamp(self.life / self.max_life * 1.6, 0, 1)
        img = assets.font(22, True).render(self.text, True, self.color)
        sh = assets.font(22, True).render(self.text, True, (0, 0, 0))
        img.set_alpha(int(255 * a))
        sh.set_alpha(int(255 * a))
        p = cam.apply_pos(self.pos)
        rect = img.get_rect(center=p)
        surf.blit(sh, rect.move(1, 1))
        surf.blit(img, rect)


class CustomerSpawner:
    def __init__(self, world):
        self.world = world
        self.timer = 6.0

    def update(self, dt):
        w = self.world
        if w.current_id != "sullar":
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer = random.uniform(8, 16)
        live = [e for e in w.entities["sullar"] if isinstance(e, Customer) and e.alive]
        if len(live) >= Cfg.CUSTOMER_MAX:
            return
        gm = w.maps["sullar"]
        used = {c.slot for c in live}
        slots = gm.anchors["customer_slots"]
        free = [i for i in range(len(slots)) if i not in used]
        if not free:
            return
        i = random.choice(free)
        w.add_entity("sullar", Customer(gm.anchors["customer_spawn"], gm.anchors["customer_door"], slots[i], i))


class BrokenScooterSpawner:
    def __init__(self, world):
        self.world = world
        self.timer = 4.0

    def update(self, dt):
        w = self.world
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer = random.uniform(10, 18)
        slots = w.maps["sullar"].anchors["repair_slots"]
        used = {e.slot for e in w.entities["sullar"]
                if isinstance(e, Scooter) and e.alive and e.state == ScooterState.BROKEN}
        free = [i for i in range(len(slots)) if i not in used]
        if free:
            self.spawn(random.choice(free))

    def spawn(self, i):
        w = self.world
        s = Scooter(ScooterState.BROKEN, Location.REPAIR_BAY, pos=w.maps["sullar"].anchors["repair_slots"][i])
        s.slot = i
        w.add_entity("sullar", s)


# =====================================================================
# 7. MISSIONS
# =====================================================================
class CrewJob:
    """What Tanu & Jass are told to do: truck -> storage along a fixed route."""

    def __init__(self, truck: BigTruck, storage: Storage, route: List[Vector2]):
        self.truck, self.storage, self.route = truck, storage, route

    def route_to_truck_from(self, pos: Vector2) -> List[Vector2]:
        idx = min(range(len(self.route)), key=lambda i: (self.route[i] - pos).length_squared())
        return self.route[idx::-1]


class Mission:
    title = ""
    crew_map: Optional[str] = None

    def __init__(self, world: "World"):
        self.world = world
        self.done = False

    def start(self): pass
    def update(self, dt): pass
    def on_map_changed(self, map_id): pass
    def objective(self) -> str: return ""
    def progress(self) -> Optional[Tuple[int, int]]: return None
    def crew_job(self) -> Optional[CrewJob]: return None
    def wants_crew(self) -> bool: return False

    def complete(self, reward: int):
        self.done = True
        self.world.player.inventory.cash += reward
        self.world.stats["missions"] += 1
        self.world.banner(f"MISSION COMPLETE: {self.title}   +{money(reward)}", Col.GOOD)


class UnloadTruckMission(Mission):
    """Mission A - Heavy Duty: big truck arrives, crew (or player) moves 20 scooters to storage."""
    TRAVEL, ARRIVE, UNLOAD = range(3)

    def __init__(self, world, map_id, storage_key, title, tag, reward=Cfg.MISSION_REWARD_UNLOAD):
        super().__init__(world)
        self.map_id, self.storage_key, self.title, self.tag, self.reward = map_id, storage_key, title, tag, reward
        self.crew_map = map_id
        self.phase = self.TRAVEL
        self.truck: Optional[BigTruck] = None

    @property
    def place(self):
        return self.world.maps[self.map_id].name

    def start(self):
        self.world.banner(f"NEW MISSION: {self.title}", Col.ACCENT)
        if self.world.current_id == self.map_id:
            self._begin_site()

    def on_map_changed(self, map_id):
        if map_id == self.map_id and self.truck is None:
            self._begin_site()

    def _begin_site(self):
        gm = self.world.maps[self.map_id]
        self.truck = BigTruck(gm.anchors["truck_entry"], gm.anchors["truck_park"], gm.anchors["truck_exit"], self.tag)
        self.world.add_entity(self.map_id, self.truck)
        self.phase = self.ARRIVE
        self.world.banner("A massive truck is rolling in...", Col.ACCENT)

    @property
    def storage(self) -> Storage:
        return self.world.storages[self.storage_key]

    @property
    def delivered(self) -> int:
        return self.storage.count_tag(self.tag)

    def update(self, dt):
        if self.phase == self.ARRIVE and self.truck and self.truck.state == VState.PARKED:
            self.phase = self.UNLOAD
            self.world.banner("Truck parked! Command Tanu & Jass - or carry scooters yourself.", Col.ACCENT)
        if self.phase == self.UNLOAD and self.delivered >= Cfg.BIG_TRUCK_CAPACITY:
            self.truck.depart()
            self.complete(self.reward)

    def objective(self):
        if self.phase == self.TRAVEL or self.world.current_id != self.map_id:
            return f"Objective: Travel to {self.place}  ({self.world.travel_hint(self.map_id)})"
        if self.phase == self.ARRIVE:
            return f"Objective: Wait for the Big Truck at {self.place}..."
        return f"Objective: Unload Big Truck at {self.place} - command Tanu & Jass (E)"

    def progress(self):
        return (self.delivered, Cfg.BIG_TRUCK_CAPACITY) if self.phase == self.UNLOAD else None

    def crew_job(self):
        if self.phase == self.UNLOAD and self.truck and self.truck.state == VState.PARKED:
            return CrewJob(self.truck, self.storage, self.world.maps[self.map_id].anchors["unload_route"])
        return None

    def wants_crew(self):
        return self.crew_job() is not None and bool(self.truck.cargo) and self.world.current_id == self.map_id


class LoadLoaderMission(Mission):
    """Mission B - The 9-Scooter Load: shed -> small loader, exactly 9, then it drives off."""
    TRAVEL, ARRIVE, LOAD, DEPART = range(4)

    def __init__(self, world, map_id="ratolan", reward=Cfg.MISSION_REWARD_LOAD):
        super().__init__(world)
        self.map_id, self.reward = map_id, reward
        self.title = "The 9-Scooter Load"
        self.crew_map = map_id
        self.phase = self.TRAVEL
        self.loader: Optional[SmallLoader] = None

    def start(self):
        self.world.banner(f"NEW MISSION: {self.title}", Col.ACCENT)
        if self.world.current_id == self.map_id:
            self._begin_site()

    def on_map_changed(self, map_id):
        if map_id == self.map_id and self.loader is None:
            self._begin_site()

    def _begin_site(self):
        gm = self.world.maps[self.map_id]
        self.loader = SmallLoader(gm.anchors["truck_entry"], gm.anchors["truck_park"], gm.anchors["truck_exit"])
        self.world.add_entity(self.map_id, self.loader)
        self.phase = self.ARRIVE
        self.world.banner("The Choti Gaadi is arriving...", Col.ACCENT)

    def update(self, dt):
        if self.phase == self.ARRIVE and self.loader.state == VState.PARKED:
            self.phase = self.LOAD
            self.world.banner("Load EXACTLY 9 scooters from the shed rack into the Choti Gaadi.", Col.ACCENT)
        if self.phase == self.LOAD and self.loader.full:
            for s in self.loader.cargo:
                s.location = Location.DEPARTED
            self.loader.depart()
            self.phase = self.DEPART
            self.world.banner("Exactly 9! The Choti Gaadi drives off...", Col.GOOD)
        if self.phase == self.DEPART and self.loader.state == VState.GONE:
            self.complete(self.reward)

    def objective(self):
        place = self.world.maps[self.map_id].name
        if self.phase == self.TRAVEL or self.world.current_id != self.map_id:
            return f"Objective: Travel to {place}  ({self.world.travel_hint(self.map_id)})"
        if self.phase == self.ARRIVE:
            return "Objective: Wait for the Choti Gaadi..."
        if self.phase == self.LOAD:
            return "Objective: Move exactly 9 scooters from the shed rack into the Choti Gaadi"
        return "Objective: Watch the Choti Gaadi leave..."

    def progress(self):
        if self.phase in (self.LOAD, self.DEPART) and self.loader:
            return len(self.loader.cargo), Cfg.LOADER_CAPACITY
        return None


class MissionManager:
    def __init__(self, world: "World", missions: List[Mission]):
        self.world = world
        self.queue = list(missions)
        self.total = len(missions)
        self.active: Optional[Mission] = None
        self.completed = 0
        self.cooldown = 1.0
        self.finished = False

    def update(self, dt):
        if self.active:
            self.active.update(dt)
            if self.active.done:
                self.completed += 1
                self.active = None
                self.cooldown = 3.5
        elif not self.finished:
            self.cooldown -= dt
            if self.cooldown <= 0:
                if self.queue:
                    self.active = self.queue.pop(0)
                    self.active.start()
                    self.world.on_mission_started(self.active)
                else:
                    self.finished = True

    def on_map_changed(self, map_id):
        m = self.active
        if m and m.crew_map == map_id:
            self.world.relocate_crew(map_id)
        elif map_id == "sullar":
            self.world.relocate_crew("sullar")
        if m:
            m.on_map_changed(map_id)

    def objective(self) -> str:
        if self.active:
            return self.active.objective()
        return "Objective: All missions done!" if self.finished else "Objective: Next mission incoming..."

    def progress(self):
        return self.active.progress() if self.active else None

    def crew_job(self):
        return self.active.crew_job() if self.active else None

    def crew_wanted(self) -> bool:
        return bool(self.active and self.active.wants_crew())


# =====================================================================
# 8. WORLD
# =====================================================================
class World:
    def __init__(self, game: "Game"):
        self.game = game
        self.assets = game.assets
        self.maps = MapFactory.build_all()
        self.entities: Dict[str, List[Entity]] = {k: [] for k in self.maps}
        self.current_id = "sullar"
        self.camera = Camera(SCREEN_W, SCREEN_H)
        self.dialogue = DialogueBox(self.assets)
        self.texts: List[FloatingText] = []
        self.banners: List[List] = []
        self.fade = Fade()
        self.stats = {"repaired": 0, "sold": 0, "missions": 0}
        self.time = 0.0
        self.input_locked = False
        self.focus: Optional[Interactable] = None
        self.debug = False
        self.victory = False
        self.victory_timer = 0.0

        self.player = Player(self.maps["sullar"].anchors["spawn_default"])
        self.player.map_id = "sullar"
        self.camera.set_bounds(self.current_map.bounds)
        self.camera.snap_to(self.player.pos)

        self.storages: Dict[str, Storage] = {}
        self.npcs: List[NPC] = []
        self._populate()

        self.broken_spawner = BrokenScooterSpawner(self)
        self.customer_spawner = CustomerSpawner(self)
        self.broken_spawner.spawn(0)
        self.broken_spawner.spawn(2)

        self.missions = MissionManager(self, [
            UnloadTruckMission(self, "ratolan", "shed", "Heavy Duty at Ratolan", "unload_ratolan"),
            LoadLoaderMission(self, "ratolan"),
            UnloadTruckMission(self, "sullar", "showroom", "Heavy Duty at Sullar Gharat", "unload_sullar"),
        ])

    # ---- setup -----------------------------------------------------------
    def _populate(self):
        show = Storage((200, 240), (200, 70), "Showroom", Location.SHOWROOM, (110, 120, 140))
        shed = Storage((400, 150), (200, 70), "Shed Rack", Location.SHED, (120, 100, 80))
        self.storages = {"showroom": show, "shed": shed}
        for _ in range(5):
            show.add(Scooter(ScooterState.BRAND_NEW))
        for _ in range(4):
            shed.add(Scooter(ScooterState.BRAND_NEW))
        self.add_entity("sullar", show)
        self.add_entity("ratolan", shed)
        self.add_entity("sullar", ToolRack((1228, 170)))

        tanu = NPC("Tanu", (300, 260), (232, 124, 60), "npc_tanu", [
            ["Jashan bhai! The showroom is spotless. Customers are starting to roll in.",
             "Sell scooters from the showroom floor - repaired ones go there automatically."],
            ["Chai peeni hai? First finish the work, then chai!"],
            ["Yakuza Electric Scooters are selling like hot samosas this season."]])
        jass = NPC("Jass", (400, 650), (150, 96, 206), "npc_jass", [
            ["Repair bay has a few broken pieces. Tool rack is on the right wall."],
            ["Ratolan Village shed is the big one. A truck is due there today, I heard."],
            ["Bhai, I could unload a truck with my eyes closed. Just say the word."]])
        self.npcs = [tanu, jass]
        for n in self.npcs:
            n.map_id = "sullar"
            self.entities["sullar"].append(n)
        self._place_crew("sullar")

    def intro(self):
        self.dialogue.start("Tanu", [
            "Jashan bhai! Welcome to Aulakh Motors - Sullar Gharat Agency.",
            "Repair the broken scooters, sell to customers, and keep the cash flowing.",
            "A big truck of Yakuza Electric Scooters reaches Ratolan Village today. Head east when you are ready!"],
            color=(232, 124, 60))

    # ---- accessors -------------------------------------------------------
    @property
    def current_map(self) -> GameMap:
        return self.maps[self.current_id]

    def add_entity(self, map_id: str, e: Entity):
        e.map_id = map_id
        self.entities[map_id].append(e)

    def crew_job(self) -> Optional[CrewJob]:
        return self.missions.crew_job()

    def crew_wanted(self, npc: NPC) -> bool:
        return npc.state not in WORK_STATES and self.missions.crew_wanted()

    def stash_scooter(self, s: Scooter, map_id: str):
        """Put a stray scooter somewhere sensible (back in truck, else map storage)."""
        job = self.crew_job()
        if job and job.truck.map_id == map_id:
            job.truck.cargo.append(s)
            s.location = Location.TRUCK
        else:
            self.storages["shed" if map_id == "ratolan" else "showroom"].add(s)

    def travel_hint(self, target: str) -> str:
        for t in self.current_map.triggers:
            if t.target_map == target:
                return t.label.replace("<-", "").replace("->", "").strip().lower() + " exit"
        return "go there"

    # ---- crew relocation -------------------------------------------------
    def _place_crew(self, map_id: str):
        crew = self.maps[map_id].anchors["crew"]
        for n in self.npcs:
            spot, zone = crew[n.name]
            n.pos = Vector2(spot)
            n.sync_rect()
            n.wander_rect = zone

    def relocate_crew(self, map_id: str):
        crew = self.maps[map_id].anchors["crew"]
        for n in self.npcs:
            if n.map_id == map_id:
                continue
            n.reset_task(self)
            if n in self.entities[n.map_id]:
                self.entities[n.map_id].remove(n)
            self.add_entity(map_id, n)
            spot, zone = crew[n.name]
            n.pos = Vector2(spot)
            n.sync_rect()
            n.wander_rect = zone

    # ---- feedback --------------------------------------------------------
    def popup(self, text, pos, color=Col.WHITE):
        self.texts.append(FloatingText(text, pos, color))

    def banner(self, text, color=Col.ACCENT):
        self.banners.append([text, 5.0, color])
        self.banners = self.banners[-3:]

    def on_mission_started(self, mission: Mission):
        pass

    # ---- interaction -----------------------------------------------------
    def _candidates(self):
        for e in self.entities[self.current_id]:
            if e.alive and isinstance(e, Interactable):
                yield e

    def update_focus(self):
        self.focus = None
        if self.input_locked or self.player.action:
            return
        best, best_d = None, Cfg.INTERACT_RADIUS
        for e in self._candidates():
            d = rect_distance(e.rect, self.player.pos)
            if d <= best_d and e.can_interact(self.player, self):
                best, best_d = e, d
        self.focus = best

    def try_interact(self):
        if self.focus and not self.input_locked:
            self.focus.interact(self.player, self)

    def try_deposit(self):
        if self.input_locked or self.player.action:
            return
        if not self.player.inventory.scooters:
            self.popup("Nothing to put down", self.player.pos + Vector2(0, -30), Col.MUTED)
            return
        best, best_d = None, Cfg.INTERACT_RADIUS + 10
        for e in self._candidates():
            d = rect_distance(e.rect, self.player.pos)
            if d <= best_d and e.accepts_deposit(self.player, self):
                best, best_d = e, d
        if best:
            best.deposit(self.player, self)
        else:
            self.popup("Nowhere to put scooters here", self.player.pos + Vector2(0, -30), Col.MUTED)

    # ---- map travel ------------------------------------------------------
    def check_triggers(self):
        if self.input_locked or self.player.action:
            return
        for t in self.current_map.triggers:
            if self.player.rect.colliderect(t.rect):
                self.fade.start(lambda t=t: self._do_travel(t))
                return

    def _do_travel(self, t: Trigger):
        self.current_id = t.target_map
        self.player.map_id = t.target_map
        self.player.pos = Vector2(t.spawn)
        self.player.vel.update(0, 0)
        self.player.sync_rect()
        self.camera.set_bounds(self.current_map.bounds)
        self.camera.snap_to(self.player.pos)
        self.focus = None
        self.banner(f"Arrived: {self.current_map.name}", Col.WHITE)
        self.missions.on_map_changed(self.current_id)

    # ---- frame -----------------------------------------------------------
    def update(self, dt: float):
        self.time += dt
        self.fade.update(dt)
        self.dialogue.update(dt)
        self.input_locked = self.dialogue.active or self.fade.active
        ents = self.entities[self.current_id]
        for e in list(ents):
            e.update(dt, self)
        ents[:] = [e for e in ents if e.alive]
        self.player.update(dt, self)
        self.broken_spawner.update(dt)
        self.customer_spawner.update(dt)
        self.missions.update(dt)
        self.check_triggers()
        self.update_focus()
        for ft in self.texts:
            ft.update(dt)
        self.texts = [t for t in self.texts if t.life > 0]
        for b in self.banners:
            b[1] -= dt
        self.banners = [b for b in self.banners if b[1] > 0]
        self.camera.update(self.player.pos, dt)
        if self.missions.finished and not self.dialogue.active:
            self.victory_timer += dt
            if self.victory_timer > 2.5:
                self.victory = True

    def draw(self, surf):
        gm = self.current_map
        gm.draw(surf, self.camera, self.assets, self.time, self.debug)
        drawables = [e for e in self.entities[self.current_id] if e.alive] + [self.player]
        for e in sorted(drawables, key=lambda e: e.rect.bottom):
            e.draw(surf, self.camera, self.assets, self)
        if self.debug:
            for e in drawables:
                pygame.draw.rect(surf, (0, 255, 0), self.camera.apply(e.rect), 1)
            pygame.draw.circle(surf, (0, 200, 255), self.camera.apply_pos(self.player.pos), Cfg.INTERACT_RADIUS, 1)
        if self.focus:
            self._draw_prompt(surf)
        for ft in self.texts:
            ft.draw(surf, self.camera, self.assets)

    def _draw_prompt(self, surf):
        text = self.focus.prompt(self.player, self)
        font = self.assets.font(20, True)
        w = font.size(text)[0] + 24
        r = self.camera.apply(self.focus.rect)
        box = pygame.Rect(0, 0, w, 32)
        box.midbottom = (r.centerx, r.top - 34)
        box.clamp_ip(pygame.Rect(8, 8, SCREEN_W - 16, SCREEN_H - 16))
        draw_panel(surf, box, (16, 18, 26), 235, Col.ACCENT, 8)
        draw_text(surf, font, text, box.center, Col.WHITE, "center", shadow=False)

    def summary(self) -> Dict[str, object]:
        return {"cash": self.player.inventory.cash, "repaired": self.stats["repaired"],
                "sold": self.stats["sold"], "missions": self.stats["missions"], "time": self.time}


# =====================================================================
# 9. HUD
# =====================================================================
class HUD:
    def __init__(self, assets: AssetManager):
        self.assets = assets

    def draw(self, surf, world: World, fps: float):
        a = self.assets
        inv = world.player.inventory
        carried = len(inv.scooters)
        show_n = len(world.storages["showroom"].scooters)
        shed_n = len(world.storages["shed"].scooters)
        total = carried + show_n + shed_n

        # --- status panel (top-left)
        panel = pygame.Rect(14, 14, 420, 150)
        draw_panel(surf, panel, alpha=205, border=(60, 66, 86))
        zone = world.current_map.zone_name_at(world.player.pos)
        draw_text(surf, a.font(16, True), "LOCATION", (panel.x + 14, panel.y + 8), Col.MUTED)
        draw_text(surf, a.font(20, True), world.current_map.name + (f" - {zone}" if zone else ""),
                  (panel.x + 14, panel.y + 24), Col.WHITE)
        draw_text(surf, a.font(26, True), f"CASH  {money(inv.cash)}", (panel.x + 14, panel.y + 56), Col.ACCENT)
        draw_text(surf, a.font(20, True), f"SCOOTERS  {total} total   (carrying {carried}/{inv.capacity})",
                  (panel.x + 14, panel.y + 90), Col.WHITE)
        draw_text(surf, a.font(16), f"Showroom {show_n}   |   Shed {shed_n}   |   Tools {inv.tools}/{Cfg.START_TOOLS}",
                  (panel.x + 14, panel.y + 118), Col.MUTED)

        # --- objective panel (top-right)
        obj_rect = pygame.Rect(SCREEN_W - 500, 14, 486, 96)
        draw_panel(surf, obj_rect, alpha=205, border=(110, 90, 20))
        draw_text(surf, a.font(16, True), f"ACTIVE OBJECTIVE   (missions {world.missions.completed}/{world.missions.total})",
                  (obj_rect.x + 14, obj_rect.y + 8), Col.ACCENT)
        font = a.font(20)
        for i, line in enumerate(wrap_text(font, world.missions.objective(), obj_rect.w - 28)[:2]):
            draw_text(surf, font, line, (obj_rect.x + 14, obj_rect.y + 30 + i * 22))
        prog = world.missions.progress()
        if prog:
            bar = pygame.Rect(obj_rect.x + 14, obj_rect.bottom - 24, obj_rect.w - 90, 14)
            draw_bar(surf, bar, prog[0] / prog[1], Col.GOOD)
            draw_text(surf, a.font(18, True), f"{prog[0]}/{prog[1]}", (bar.right + 10, bar.centery), Col.WHITE, "midleft")

        # --- banners
        for i, (text, t, color) in enumerate(world.banners):
            alpha = int(255 * clamp(t / 1.0, 0, 1))
            img = a.font(22 if TOUCH_MODE else 26, True).render(text, True, color)
            img.set_alpha(alpha)
            r = img.get_rect(midtop=(SCREEN_W // 2, 178 + i * 34))
            bg = pygame.Surface((r.w + 24, r.h + 8), pygame.SRCALPHA)
            pygame.draw.rect(bg, (0, 0, 0, int(150 * alpha / 255)), bg.get_rect(), border_radius=8)
            surf.blit(bg, (r.x - 12, r.y - 4))
            surf.blit(img, r)

        # --- controls hint
        if not TOUCH_MODE:
            draw_text(surf, a.font(16), "WASD move   E interact   Q put down scooters   ESC pause   F3 debug",
                      (14, SCREEN_H - 12), Col.MUTED, "bottomleft")
        if world.debug:
            draw_text(surf, a.font(18, True), f"FPS {fps:4.0f}", (SCREEN_W - 14, SCREEN_H - 12), Col.GOOD, "bottomright")


# =====================================================================
# 10. GAME STATE MANAGER + STATES
# =====================================================================
class State:
    transparent = False   # if True, the state beneath is drawn first (overlays)

    def __init__(self, game: "Game"):
        self.game = game

    def enter(self, **kw): pass
    def exit(self): pass
    def resume(self): pass
    def handle_event(self, event): pass
    def handle_tap(self, pos): pass          # touch / click in canvas coordinates
    def update(self, dt): pass
    def draw(self, surf): pass


class GameStateManager:
    def __init__(self, game):
        self.game = game
        self.registry: Dict[str, Callable[[], State]] = {}
        self.stack: List[State] = []

    def register(self, name: str, factory: Callable[["Game"], State]):
        self.registry[name] = lambda: factory(self.game)

    def push(self, name, **kw):
        st = self.registry[name]()
        self.stack.append(st)
        st.enter(**kw)

    def pop(self):
        if self.stack:
            self.stack.pop().exit()
        if self.stack:
            self.stack[-1].resume()

    def switch(self, name, **kw):
        while self.stack:
            self.stack.pop().exit()
        self.push(name, **kw)

    @property
    def current(self) -> Optional[State]:
        return self.stack[-1] if self.stack else None

    def handle_event(self, e):
        if self.current:
            self.current.handle_event(e)

    def update(self, dt):
        if self.current:
            self.current.update(dt)

    def draw(self, surf):
        start = len(self.stack) - 1
        while start > 0 and self.stack[start].transparent:
            start -= 1
        for st in self.stack[max(0, start):]:
            st.draw(surf)


class Menu:
    """Vertical menu: keyboard (W/S/Enter) and touch (tap an item)."""

    def __init__(self, assets, items: List[Tuple[str, Callable]]):
        self.assets, self.items, self.sel = assets, items, 0
        self.rects: List[pygame.Rect] = []

    def handle_event(self, e):
        if e.type != pygame.KEYDOWN:
            return
        if e.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(self.items)
        elif e.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(self.items)
        elif e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_e):
            self.items[self.sel][1]()

    def tap(self, pos) -> bool:
        for i, r in enumerate(self.rects):
            if r.collidepoint(pos):
                self.sel = i
                self.items[i][1]()
                return True
        return False

    def draw(self, surf, cx, y):
        self.rects = []
        for i, (label, _) in enumerate(self.items):
            sel = i == self.sel
            font = self.assets.font(34 if sel else 30, True)
            col = Col.ACCENT if sel else Col.WHITE
            r = draw_text(surf, font, ("> " if sel else "") + label, (cx, y + i * 52), col, "midtop")
            self.rects.append(r.inflate(240, 22))                    # generous touch target


class MainMenuState(State):
    def enter(self, **kw):
        self.t = 0.0
        self.show_help = False
        self.menu = Menu(self.game.assets, [
            ("Start Game", lambda: self.game.states.switch("playing")),
            ("How to Play", lambda: setattr(self, "show_help", True)),
            ("Quit", self.game.quit)])
        self.rng = random.Random(7)
        self.cars = [[self.rng.uniform(0, SCREEN_W), self.rng.uniform(80, SCREEN_H - 80),
                      self.rng.uniform(40, 120)] for _ in range(14)]

    def handle_event(self, e):
        if self.show_help:
            if e.type == pygame.KEYDOWN:
                self.show_help = False
            return
        self.menu.handle_event(e)

    def update(self, dt):
        self.t += dt
        for c in self.cars:
            c[0] = (c[0] + c[2] * dt) % (SCREEN_W + 80)

    def draw(self, surf):
        a = self.game.assets
        surf.fill((16, 20, 30))
        for c in self.cars:
            pygame.draw.rect(surf, (255, 196, 0), (c[0] - 80, c[1], 36, 20), border_radius=6)
            pygame.draw.circle(surf, (30, 30, 36), (int(c[0] - 72), int(c[1] + 20)), 5)
            pygame.draw.circle(surf, (30, 30, 36), (int(c[0] - 52), int(c[1] + 20)), 5)
        veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        veil.fill((10, 12, 20, 150))
        surf.blit(veil, (0, 0))
        bob = int(math.sin(self.t * 2) * 5)
        top = 36 if TOUCH_MODE else 130
        draw_text(surf, a.font(64 if TOUCH_MODE else 76, True), "AULAKH MOTORS",
                  (SCREEN_W // 2, top + bob), Col.ACCENT, "midtop")
        draw_text(surf, a.font(36 if TOUCH_MODE else 40, True), "S I M U L A T O R",
                  (SCREEN_W // 2, top + 80 + bob), Col.WHITE, "midtop")
        draw_text(surf, a.font(20), "Sullar Gharat  <->  Ratolan Village", (SCREEN_W // 2, top + 142), Col.MUTED, "midtop")
        if self.show_help:
            self._draw_help(surf)
        else:
            self.menu.draw(surf, SCREEN_W // 2, top + 215)

    def handle_tap(self, pos):
        if self.show_help:
            self.show_help = False
        else:
            self.menu.tap(pos)

    def _draw_help(self, surf):
        a = self.game.assets
        if TOUCH_MODE:
            box = pygame.Rect(30, 20, SCREEN_W - 60, SCREEN_H - 40)
            lines = ["Drag on the LEFT side of the screen to move (joystick).",
                     "E button: interact / talk / sell / repair / pick up.",
                     "Q button: put scooters down at a rack or the loader.",
                     "Repair broken scooters (3s) - they go to the showroom.",
                     "Tap E near customers to sell. Command Tanu & Jass to unload.",
                     "The Choti Gaadi holds EXACTLY 9 scooters.",
                     "Walk into the yellow zones to travel between maps.",
                     "", "Tap anywhere to go back"]
            step, fs, y0 = 40, 21, 28
        else:
            box = pygame.Rect(240, 320, SCREEN_W - 480, 340)
            lines = ["WASD / Arrows - move          E - interact / talk          Q - put scooters down",
                     "Repair broken scooters (3s) -> they go to the showroom and earn cash.",
                     "Customers wait in the showroom: press E on them to sell a scooter.",
                     "Big Truck: command Tanu & Jass (E) or carry scooters yourself to the rack.",
                     "Choti Gaadi holds EXACTLY 9 - extra scooters are rejected.",
                     "Walk through the glowing yellow zones to travel between maps.",
                     "", "Press any key to go back"]
            step, fs, y0 = 38, 21, 22
        draw_panel(surf, box, alpha=235, border=Col.ACCENT)
        for i, l in enumerate(lines):
            draw_text(surf, a.font(fs), l, (box.centerx, box.y + y0 + i * step), Col.WHITE if l else Col.MUTED, "midtop")


class PlayingState(State):
    def enter(self, **kw):
        self.world = World(self.game)
        self.hud = HUD(self.game.assets)
        self.world.intro()
        self.game.touch.reset()

    def resume(self):
        self.game.touch.reset()

    def handle_event(self, e):
        if e.type != pygame.KEYDOWN:
            return
        if e.key in BACK_KEYS:
            self.game.states.push("pause")
        elif e.key in (pygame.K_e, pygame.K_SPACE):
            if self.world.dialogue.active:
                self.world.dialogue.advance()
            elif e.key == pygame.K_e:
                self.world.try_interact()
        elif e.key == pygame.K_q:
            self.world.try_deposit()
        elif e.key == pygame.K_F3:
            self.world.debug = not self.world.debug

    def update(self, dt):
        self.world.update(dt)
        if self.world.victory:
            self.game.states.switch("accomplished", stats=self.world.summary())

    def draw(self, surf):
        self.world.draw(surf)
        self.hud.draw(surf, self.world, self.game.clock.get_fps())
        self.world.dialogue.draw(surf)
        if not self.world.dialogue.active:
            self.game.touch.draw(surf, self.game.assets)
        self.world.fade.draw(surf)


class PauseState(State):
    transparent = True

    def enter(self, **kw):
        self.menu = Menu(self.game.assets, [
            ("Resume", self.game.states.pop),
            ("Main Menu", lambda: self.game.states.switch("menu")),
            ("Quit", self.game.quit)])

    def handle_event(self, e):
        if e.type == pygame.KEYDOWN and e.key in BACK_KEYS:
            self.game.states.pop()
            return
        self.menu.handle_event(e)

    def handle_tap(self, pos):
        self.menu.tap(pos)

    def draw(self, surf):
        veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        veil.fill((6, 8, 14, 170))
        surf.blit(veil, (0, 0))
        draw_text(surf, self.game.assets.font(64, True), "PAUSED", (SCREEN_W // 2, 190), Col.ACCENT, "midtop")
        self.menu.draw(surf, SCREEN_W // 2, 320)


class MissionAccomplishedState(State):
    def enter(self, stats=None, **kw):
        self.stats = stats or {}
        self.t = 0.0

    def handle_event(self, e):
        if e.type == pygame.KEYDOWN and e.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_e) and self.t > 1.0:
            self.game.states.switch("menu")

    def handle_tap(self, pos):
        if self.t > 1.0:
            self.game.states.switch("menu")

    def update(self, dt):
        self.t += dt

    def draw(self, surf):
        a = self.game.assets
        surf.fill((14, 24, 20))
        for i in range(40):                                        # confetti
            x = (i * 97 + self.t * (30 + i % 7 * 9)) % SCREEN_W
            y = (i * 53 + self.t * (60 + i % 5 * 22)) % SCREEN_H
            pygame.draw.rect(surf, [(255, 196, 0), (96, 222, 134), (90, 200, 255), (242, 96, 96)][i % 4], (x, y, 8, 8))
        small = TOUCH_MODE
        draw_text(surf, a.font(56 if small else 78, True), "MISSION ACCOMPLISHED",
                  (SCREEN_W // 2, 40 if small else 120), Col.GOOD, "midtop")
        s = self.stats
        mins, secs = divmod(int(s.get("time", 0)), 60)
        rows = [("Final cash", money(int(s.get("cash", 0)))), ("Scooters repaired", str(s.get("repaired", 0))),
                ("Scooters sold", str(s.get("sold", 0))), ("Missions completed", str(s.get("missions", 0))),
                ("Time played", f"{mins}m {secs:02d}s")]
        y0, step, fs = (140, 46, 28) if small else (270, 50, 32)
        for i, (k, v) in enumerate(rows):
            draw_text(surf, a.font(fs), k, (SCREEN_W // 2 - 20, y0 + i * step), Col.MUTED, "topright")
            draw_text(surf, a.font(fs, True), v, (SCREEN_W // 2 + 20, y0 + i * step), Col.WHITE, "topleft")
        if self.t > 1.0:
            msg = "Tap to return to the menu" if TOUCH_MODE else "Press ENTER to return to the menu"
            draw_text(surf, a.font(26), msg, (SCREEN_W // 2, SCREEN_H - 60), Col.ACCENT, "midtop")


# =====================================================================
# 11. GAME (MAIN LOOP)
# =====================================================================
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption(TITLE)
        if IS_ANDROID:
            pygame.display.set_mode((0, 0), pygame.FULLSCREEN)          # native phone resolution
        else:
            pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
        # The game always renders to a fixed-size canvas that is letterboxed onto the real screen,
        # so the same code works on any phone aspect ratio.
        self.canvas = pygame.Surface((SCREEN_W, SCREEN_H))
        self.screen = self.canvas
        self._scaled: Optional[pygame.Surface] = None
        self.view_rect = pygame.Rect(0, 0, SCREEN_W, SCREEN_H)
        self.clock = pygame.time.Clock()
        self.assets = AssetManager()
        self.input = Input()
        self.touch = TouchControls(TOUCH_MODE, emulate_mouse=TOUCH_MODE and not IS_ANDROID)
        self.running = True
        self.states = GameStateManager(self)
        self.states.register("menu", MainMenuState)
        self.states.register("playing", PlayingState)
        self.states.register("pause", PauseState)
        self.states.register("accomplished", MissionAccomplishedState)
        self.states.push("menu")

    def quit(self):
        self.running = False

    def _fire(self, key):
        """Touch buttons act like key presses."""
        self.states.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key))

    def _on_touch(self, kind, fid, pos):
        cur = self.states.current
        if isinstance(cur, PlayingState):
            if kind == "down":
                if cur.world.dialogue.active:
                    self._fire(pygame.K_e)                 # tap anywhere advances dialogue
                else:
                    self.touch.down(fid, pos, self._fire)
            elif kind == "move":
                self.touch.move(fid, pos)
            else:
                self.touch.up(fid)
        elif kind == "down" and cur:
            cur.handle_tap(pos)
        if not isinstance(self.states.current, PlayingState):
            self.touch.reset()

    def present(self):
        win = pygame.display.get_surface()
        ww, wh = win.get_size()
        k = min(ww / SCREEN_W, wh / SCREEN_H)
        w, h = max(1, int(SCREEN_W * k)), max(1, int(SCREEN_H * k))
        self.view_rect = pygame.Rect((ww - w) // 2, (wh - h) // 2, w, h)
        if (w, h) == (SCREEN_W, SCREEN_H) and (ww, wh) == (w, h):
            win.blit(self.canvas, (0, 0))
        else:
            if self._scaled is None or self._scaled.get_size() != (w, h):
                self._scaled = pygame.Surface((w, h), 0, self.canvas)
            pygame.transform.scale(self.canvas, (w, h), self._scaled)
            win.fill((0, 0, 0))
            win.blit(self._scaled, self.view_rect)
        pygame.display.flip()

    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)      # clamp: no tunnelling after a hitch
            self.input.begin_frame()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    continue
                t = self.touch.convert(event, self.view_rect)
                if t:
                    self._on_touch(*t)
                    continue
                self.input.feed(event)
                self.states.handle_event(event)
            self.input.touch_axis = self.touch.axis
            self.states.update(dt)
            self.states.draw(self.canvas)
            self.present()
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    Game().run()
