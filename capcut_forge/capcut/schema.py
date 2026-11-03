"""Builders for CapCut draft JSON objects.

Field sets mirror what CapCut 9.3 (draft version 360000) writes itself. CapCut
fills in anything it does not find, but a draft that *looks* native imports
most reliably, so these stay close to the app's own output. Times are
microseconds; geometry is normalised to the canvas (-1..1, +y up).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

US = 1_000_000
PHOTO_DURATION_US = 10_800_000_000   # what CapCut stores for a still image
TEXT_RENDER_INDEX = 14_000           # CapCut keeps text in a high z band


def uid() -> str:
    return str(uuid.uuid4()).upper()


def us(seconds: float) -> int:
    return round(seconds * US)


MATERIAL_CATEGORIES = [
    "ai_text_effects", "ai_translates", "audio_balances", "audio_effects", "audio_fades",
    "audio_pannings", "audio_pitch_shifts", "audio_track_indexes", "audios", "beats",
    "canvases", "chromas", "color_curves", "common_mask", "digital_human_model_dressing",
    "digital_humans", "drafts", "effects", "flowers", "green_screens", "handwrites", "hsl",
    "hsl_curves", "images", "log_color_wheels", "loudnesses", "manual_beautys",
    "manual_deformations", "material_animations", "material_colors", "multi_language_refs",
    "placeholder_infos", "placeholders", "plugin_effects", "primary_color_wheels",
    "realtime_denoises", "shapes", "smart_crops", "smart_relights",
    "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates",
    "texts", "time_marks", "transitions", "video_effects", "video_radius", "video_shadows",
    "video_strokes", "video_trackings", "videos", "vocal_beautifys", "vocal_separations",
]


# ------------------------------------------------------------------- materials


def video_material(path: Path, width: int, height: int, duration_us: int,
                   has_audio: bool, photo: bool = False) -> dict:
    return {
        "id": uid(), "unique_id": "", "type": "photo" if photo else "video",
        "duration": PHOTO_DURATION_US if photo else duration_us,
        "path": str(path), "media_path": "", "local_id": "", "has_audio": has_audio,
        "reverse_path": "", "intensifies_path": "", "reverse_intensifies_path": "",
        "intensifies_audio_path": "", "cartoon_path": "", "width": width, "height": height,
        "category_id": "", "category_name": "local", "material_id": "",
        "material_name": path.name, "material_url": "",
        "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0,
                 "upper_right_y": 0.0, "lower_left_x": 0.0, "lower_left_y": 1.0,
                 "lower_right_x": 1.0, "lower_right_y": 1.0},
        "crop_ratio": "free", "audio_fade": None, "crop_scale": 1.0, "extra_type_option": 0,
        "stable": {"stable_level": 0, "matrix_path": "",
                   "time_range": {"start": 0, "duration": 0}},
        "matting": {"flag": 0, "path": "", "interactiveTime": [],
                    "has_use_quick_brush": False, "strokes": [],
                    "has_use_quick_eraser": False, "expansion": 0, "feather": 0,
                    "reverse": False, "custom_matting_id": "",
                    "enable_matting_stroke": False, "is_clould": False,
                    "mask_video_path": "", "cloud_product_fps": 0.0},
        "source": 0, "source_platform": 0, "formula_id": "", "check_flag": 62978047,
        "video_algorithm": {
            "algorithms": [], "time_range": None, "path": "", "gameplay_configs": [],
            "ai_in_painting_config": [], "complement_frame_config": None,
            "motion_blur_config": None, "deflicker": None, "noise_reduction": None,
            "quality_enhance": None, "super_resolution": None,
            "ai_background_configs": [], "smart_complement_frame": None,
            "aigc_generate": None, "aigc_generate_list": [], "mouth_shape_driver": None,
            "ai_expression_driven": None, "ai_motion_driven": None,
            "image_interpretation": None,
            "story_video_modify_video_config": {
                "task_id": "", "is_overwrite_last_video": False, "tracker_task_id": "",
                "generate_id": "", "generate_card_id": ""},
            "skip_algorithm_index": []},
        "is_unified_beauty_mode": False, "is_set_beauty_mode": False,
        "object_locked": None, "smart_motion": None, "multi_camera_info": None,
        "freeze": None, "picture_from": "none", "picture_set_category_id": "",
        "picture_set_category_name": "", "team_id": "",
        "local_material_id": str(uuid.uuid4()), "origin_material_id": "", "request_id": "",
        "has_sound_separated": False, "is_text_edit_overdub": False,
        "is_ai_generate_content": False, "is_video_copilot_aigc_content": False,
        "aigc_type": "none", "is_copyright": False, "aigc_history_id": "",
        "aigc_item_id": "", "local_material_from": "", "smart_match_info": None,
        "beauty_face_preset_infos": [], "beauty_body_preset_id": "",
        "beauty_face_auto_preset": {"preset_id": "", "name": "", "rate_map": "", "scene": ""},
        "beauty_face_auto_preset_infos": [], "beauty_body_auto_preset": None,
        "live_photo_timestamp": -1, "live_photo_cover_path": "",
        "content_feature_info": None, "corner_pin": None, "surface_trackings": [],
        "video_mask_stroke": {"resource_id": "", "path": "", "type": "", "color": "",
                              "size": 0.0, "alpha": 0.0, "distance": 0.0, "texture": 0.0,
                              "horizontal_shift": 0.0, "vertical_shift": 0.0},
        "video_mask_shadow": {"resource_id": "", "path": "", "color": "", "alpha": 0.0,
                              "blur": 0.0, "distance": 0.0, "angle": 0.0},
        "pre_applied_vip_materials": [], "workflow_node_id": "",
    }


def audio_material(path: Path, duration_us: int) -> dict:
    return {
        "id": uid(), "unique_id": "", "type": "extract_music", "name": path.name,
        "duration": duration_us, "path": str(path), "category_name": "local",
        "wave_points": [], "music_id": str(uuid.uuid4()), "app_id": 0, "text_id": "",
        "tone_type": "", "source_platform": 0, "video_id": "", "effect_id": "",
        "resource_id": "", "third_resource_id": "", "category_id": "",
        "intensifies_path": "", "formula_id": "", "check_flag": 1, "team_id": "",
        "local_material_id": str(uuid.uuid4()), "tone_speaker": "",
        "mock_tone_speaker": "", "tone_effect_id": "", "tone_effect_name": "",
        "tone_platform": "", "cloned_model_type": "", "tone_category_id": "",
        "tone_category_name": "", "tone_second_category_id": "",
        "tone_second_category_name": "", "tone_emotion_name_key": "",
        "tone_emotion_style": "", "tone_emotion_role": "", "tone_emotion_selection": "",
        "tone_emotion_scale": 0.0, "moyin_emotion": "", "request_id": "", "query": "",
        "search_id": "", "sound_separate_type": "", "is_text_edit_overdub": False,
        "is_ugc": False, "is_ai_clone_tone": False, "is_ai_clone_tone_post": False,
        "source_from": "", "copyright_limit_type": "none", "aigc_history_id": "",
        "aigc_item_id": "", "music_source": "", "pgc_id": "", "pgc_name": "",
        "similiar_music_info": {"original_song_id": "", "original_song_name": ""},
        "ai_music_type": 0, "ai_music_enter_from": "", "lyric_type": 0,
        "tts_task_id": "", "tts_generate_scene": "", "ai_music_generate_scene": 0,
        "tts_benefit_info": {"benefit_type": "none", "benefit_log_id": "",
                             "benefit_log_extra": "", "benefit_amount": -1},
    }


def _rgb(hex_color: str) -> list[float]:
    h = hex_color.lstrip("#")
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)]


def text_material(text: str, style) -> dict:
    """`style` is a capcut_forge.styles.CaptionStyle. The `content` field is
    JSON-in-JSON carrying the styled run; its range must cover the string or
    CapCut renders the text unstyled."""
    run = {
        "fill": {"content": {"solid": {"color": _rgb(style.color)}, "render_type": "solid"}},
        "range": [0, len(text)],
        "useLetterColor": True,
        "size": style.capcut_size,
        "font": {"path": style.capcut_font_path or "", "id": ""},
    }
    if style.stroke_width:
        run["strokes"] = [{"content": {"solid": {"color": _rgb(style.stroke_color)},
                                       "render_type": "solid"},
                           "width": style.capcut_stroke}]
    has_box = style.box_color is not None
    return {
        "recognize_task_id": "", "id": uid(), "name": "", "recognize_text": "",
        "recognize_model": "", "punc_model": "", "type": "text",
        "content": json.dumps({"styles": [run], "text": text}, ensure_ascii=False),
        "base_content": "", "words": {"start_time": [], "end_time": [], "text": []},
        "current_words": {"start_time": [], "end_time": [], "text": []},
        "global_alpha": 1.0, "combo_info": {"text_templates": []},
        "caption_template_info": {"resource_id": "", "third_resource_id": "",
                                  "resource_name": "", "category_id": "",
                                  "category_name": "", "effect_id": "", "request_id": "",
                                  "path": "", "is_new": False, "source_platform": 0},
        "layer_weight": 1, "letter_spacing": 0.0, "text_curve": None,
        "text_loop_on_path": False, "offset_on_path": 0.0,
        "enable_path_typesetting": False, "text_exceeds_path_process_type": 0,
        "text_typesetting_paths": None, "text_typesetting_paths_file": "",
        "text_typesetting_path_index": 0, "line_spacing": 0.02, "has_shadow": False,
        "shadow_color": "", "shadow_alpha": 0.9, "shadow_smoothing": 0.45,
        "shadow_distance": 5.0, "shadow_point": {"x": 0.6364, "y": -0.6364},
        "shadow_angle": -45.0, "shadow_thickness_projection_enable": False,
        "shadow_thickness_projection_angle": 0.0,
        "shadow_thickness_projection_distance": 0.0, "border_alpha": 1.0,
        "border_color": style.stroke_color if style.stroke_width else "",
        "border_width": style.capcut_stroke if style.stroke_width else 0.08,
        "border_mode": 0, "style_name": "", "text_color": style.color, "text_alpha": 1.0,
        "font_name": "", "font_title": "none", "font_size": float(style.capcut_size),
        "font_path": style.capcut_font_path or "", "font_id": "", "font_resource_id": "",
        "initial_scale": 1.0, "font_url": "", "typesetting": 0, "alignment": 1,
        "line_feed": 1, "use_effect_default_color": False, "is_rich_text": False,
        "shape_clip_x": False, "shape_clip_y": False, "ktv_color": "",
        "text_to_audio_ids": [], "bold_width": 0.0, "italic_degree": 0,
        "underline": False, "underline_width": 0.05, "underline_offset": 0.22,
        "sub_type": 0, "check_flag": 23 if has_box else 7, "text_size": 30,
        "font_category_name": "", "font_source_platform": 0,
        "font_third_resource_id": "", "font_category_id": "", "add_type": 0,
        "operation_type": 0, "recognize_type": 0, "fonts": [],
        "background_color": style.box_color or "", "background_alpha": 1.0,
        "background_style": 1 if has_box else 0,
        "background_round_radius": style.capcut_radius if has_box else 0.0,
        "background_width": 0.14, "background_height": 0.14,
        "background_vertical_offset": 0.0, "background_horizontal_offset": 0.0,
        "background_fill": "", "single_char_bg_enable": False,
        "single_char_bg_color": "", "single_char_bg_alpha": 1.0,
        "single_char_bg_round_radius": 0.3, "single_char_bg_width": 0.0,
        "single_char_bg_height": 0.0, "single_char_bg_vertical_offset": 0.0,
        "single_char_bg_horizontal_offset": 0.0, "font_team_id": "",
        "tts_auto_update": False, "text_preset_resource_id": "", "group_id": "",
        "preset_id": "", "preset_name": "", "preset_category": "",
        "preset_category_id": "", "preset_index": 0, "preset_has_set_alignment": False,
        "force_apply_line_max_width": False, "language": "", "relevance_segment": [],
        "original_size": [], "fixed_width": -1.0, "fixed_height": -1.0,
        "autoAdaptCanvasEnabled": False, "line_max_width": 0.82, "oneline_cutoff": False,
        "cutoff_postfix": "", "subtitle_template_original_fontsize": 0.0,
        "subtitle_keywords": None, "inner_padding": -1.0,
        "multi_language_current": "none", "source_from": "", "is_lyric_effect": False,
        "lyric_group_id": "",
        "lyrics_template": {"resource_id": "", "resource_name": "", "panel": "",
                            "effect_id": "", "path": "", "category_id": "",
                            "category_name": "", "request_id": ""},
        "is_batch_replace": False, "is_words_linear": False, "ssml_content": "",
        "subtitle_keywords_config": None, "sub_template_id": -1,
        "translate_original_text": "",
    }


def text_content(material: dict) -> str:
    return json.loads(material["content"]).get("text", "")


# ------------------------------------------------------------------- helpers


def _extras(materials: dict, kind: str, fade: tuple[int, int] | None = None) -> list[str]:
    """The per-segment helper materials CapCut writes alongside each segment."""
    rows = [("speeds", {"type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None}),
            ("placeholder_infos", {"type": "placeholder_info", "meta_type": "none",
                                   "res_path": "", "res_text": "", "error_path": "",
                                   "error_text": ""})]
    if kind == "video":
        rows += [
            ("canvases", {"type": "canvas_color", "color": "", "blur": 0.0, "image": "",
                          "album_image": "", "image_id": "", "image_name": "",
                          "source_platform": 0, "team_id": ""}),
            ("material_animations", {"type": "sticker_animation", "animations": [],
                                     "multi_language_current": "none"}),
            ("sound_channel_mappings", {"type": "none", "audio_channel_mapping": 0,
                                        "is_config_open": False}),
            ("material_colors", {"is_color_clip": False, "is_gradient": False,
                                 "solid_color": "", "gradient_colors": [],
                                 "gradient_percents": [], "gradient_angle": 90.0,
                                 "width": 0.0, "height": 0.0}),
            ("loudnesses", {"enable": False, "time_range": None, "file_id": "",
                            "target_loudness": 0.0, "loudness_param": None}),
            ("vocal_separations", {"type": "vocal_separation", "choice": 0,
                                   "removed_sounds": [], "time_range": None,
                                   "production_path": "", "final_algorithm": "",
                                   "enter_from": ""}),
        ]
    elif kind == "audio":
        if fade:
            rows.append(("audio_fades", {"type": "audio_fade", "fade_type": 0,
                                         "fade_in_duration": fade[0],
                                         "fade_out_duration": fade[1]}))
        rows += [
            ("sound_channel_mappings", {"type": "none", "audio_channel_mapping": 0,
                                        "is_config_open": False}),
            ("vocal_separations", {"type": "vocal_separation", "choice": 0,
                                   "removed_sounds": [], "time_range": None,
                                   "production_path": "", "final_algorithm": "",
                                   "enter_from": ""}),
        ]
    elif kind == "text":
        rows = [("material_animations", {"type": "sticker_animation", "animations": [],
                                         "multi_language_current": "none"})]
    ids = []
    for cat, entry in rows:
        eid = uid()
        materials.setdefault(cat, []).append({"id": eid, **entry})
        ids.append(eid)
    return ids


ADJUST_EFFECT_ID = "7501974767453474064"   # CapCut's built-in Adjust panel


def adjust_materials(materials: dict, grade: dict[str, float], bundle: str) -> list[str]:
    """One `effects` material per Adjust slider, as the app writes them.
    Returns ids to append to a video segment's extra_material_refs."""
    ids = []
    for slider, value in grade.items():
        eid = uid()
        materials.setdefault("effects", []).append({
            "id": eid, "effect_id": ADJUST_EFFECT_ID, "resource_id": ADJUST_EFFECT_ID,
            "third_resource_id": "", "name": "", "report_name": "", "type": slider,
            "sub_type": "none", "path": bundle, "value": float(value), "visible": True,
            "item_effect_type": 0, "category_id": "", "category_name": "",
            "category_key": "", "sub_category_id": "", "sub_category_name": "",
            "platform": "all", "apply_target_type": 0, "source_platform": 1,
            "version": "v2", "adjust_params": [], "time_range": None, "formula_id": "",
            "enable_skin_tone_correction": False, "algorithm_artifact_path": "",
            "intensity_key": "", "face_adjust_params": [], "exclusion_group": [],
            "panel_id": "", "bloom_params": None, "request_id": "",
            "color_match_info": {"target_feature_path": "", "source_feature_path": "",
                                 "target_image_path": ""},
            "multi_language_current": "", "lumi_hub_path": f"{bundle}/lumi_hub_path",
            "covering_relation_change": 0, "beauty_face_auto_preset_id": "",
            "beauty_body_auto_preset_id": "",
            "beauty_face_auto_retouch_info": {"face_id": [],
                                              "beauty_face_auto_retouch_id": ""},
            "smart_color_mode": 0, "is_from_intelligent_quality": False,
        })
        ids.append(eid)
    return ids


def _clip(scale=1.0, x=0.0, y=0.0, rotation=0.0, alpha=1.0) -> dict:
    return {"scale": {"x": scale, "y": scale}, "rotation": rotation,
            "transform": {"x": x, "y": y},
            "flip": {"vertical": False, "horizontal": False}, "alpha": alpha}


def segment(material_id: str, refs: list[str], src_start: int, duration: int,
            tgt_start: int, kind: str = "video", volume: float = 1.0,
            render_index: int = 0, track_render_index: int = 0,
            scale=1.0, x=0.0, y=0.0) -> dict:
    visual = kind != "audio"
    return {
        "id": uid(),
        "source_timerange": {"start": src_start, "duration": duration},
        "target_timerange": {"start": tgt_start, "duration": duration},
        "render_timerange": {"start": 0, "duration": 0},
        "desc": "", "state": 0, "speed": 1.0, "is_loop": False, "is_tone_modify": False,
        "reverse": False, "intensifies_audio": False, "cartoon": False,
        "volume": volume, "last_nonzero_volume": volume or 1.0,
        "clip": _clip(scale, x, y) if visual else None,
        "uniform_scale": {"on": True, "value": 1.0} if visual else None,
        "material_id": material_id, "extra_material_refs": refs,
        "render_index": render_index, "keyframe_refs": [],
        "enable_lut": kind == "video", "enable_adjust": kind == "video",
        "enable_hsl": False, "visible": True, "group_id": "",
        "enable_color_curves": True, "enable_hsl_curves": True,
        "track_render_index": track_render_index,
        "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000} if kind == "video" else None,
        "hdr_vivid_settings": None, "enable_color_wheels": True, "track_attribute": 0,
        "is_placeholder": False, "template_id": "", "enable_smart_color_adjust": False,
        "template_scene": "default", "common_keyframes": [], "caption_info": None,
        "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0,
                              "horizontal_pos_layout": 0, "vertical_pos_layout": 0},
        "enable_color_match_adjust": False, "enable_color_correct_adjust": False,
        "enable_adjust_mask": False, "raw_segment_id": "", "lyric_keyframes": None,
        "enable_video_mask": True, "digital_human_template_group_id": "",
        "color_correct_alg_result": "", "source": "segmentsourcenormal",
        "enable_mask_stroke": False, "enable_mask_shadow": False,
        "enable_color_adjust_pro": False, "segment_color_tag": "",
    }


def track(kind: str, flag: int = 0) -> dict:
    """kind: video|audio|text. Video overlay tracks carry flag 2; main/text/audio 0."""
    return {"id": uid(), "type": kind, "segments": [], "flag": flag, "attribute": 0,
            "name": "", "is_default_name": True}


KF_TYPES = {"scale": ("KFTypeScaleX", "KFTypeScaleY"), "x": ("KFTypePositionX",),
            "y": ("KFTypePositionY",), "rotation": ("KFTypeRotation",),
            "opacity": ("KFTypeAlpha",), "volume": ("KFTypeVolume",)}


def add_keyframe(seg: dict, prop: str, timeline_us: int, value: float,
                 curve: str = "Line") -> None:
    """`time_offset` lives on the SOURCE media clock, not segment-relative:
    offset = source in-point + (t - segment start). Getting this wrong makes
    CapCut silently collapse the animation to one static value."""
    start = seg["target_timerange"]["start"]
    dur = seg["target_timerange"]["duration"]
    rel = timeline_us - start
    if not 0 <= rel <= dur:
        raise ValueError(f"{timeline_us / US:.2f}s is outside the segment "
                         f"({start / US:.2f}s–{(start + dur) / US:.2f}s)")
    offset = (seg.get("source_timerange") or {}).get("start", 0) + rel
    kfs = seg.setdefault("common_keyframes", [])
    for ptype in KF_TYPES[prop]:
        entry = next((e for e in kfs if e["property_type"] == ptype), None)
        if entry is None:
            entry = {"id": uid(), "material_id": "", "property_type": ptype,
                     "keyframe_list": []}
            kfs.append(entry)
        point = {"id": uid(), "curveType": curve, "time_offset": offset,
                 "left_control": {"x": 0.0, "y": 0.0},
                 "right_control": {"x": 0.0, "y": 0.0},
                 "values": [float(value)], "string_value": "", "graphID": ""}
        entry["keyframe_list"] = sorted(
            [p for p in entry["keyframe_list"] if p["time_offset"] != offset] + [point],
            key=lambda p: p["time_offset"])


def empty_draft(width: int, height: int, fps: float, platform: dict) -> dict:
    return {
        "id": uid(), "version": 360000, "new_version": "187.0.0", "name": "",
        "duration": 0, "create_time": 0, "update_time": 0, "fps": fps,
        "is_drop_frame_timecode": False, "color_space": 0,
        "config": {"video_mute": False, "record_audio_last_index": 1,
                   "extract_audio_last_index": 1, "original_sound_last_index": 1,
                   "subtitle_recognition_id": "", "subtitle_taskinfo": [],
                   "lyrics_recognition_id": "", "lyrics_taskinfo": [],
                   "subtitle_sync": True, "lyrics_sync": True, "voice_change_sync": False,
                   "sticker_max_index": 1, "adjust_max_index": 1, "material_save_mode": 0,
                   "export_range": None, "maintrack_adsorb": True,
                   "combination_max_index": 1, "attachment_info": [],
                   "zoom_info_params": None, "system_font_list": [],
                   "multi_language_mode": "none", "multi_language_main": "none",
                   "multi_language_current": "none", "multi_language_list": [],
                   "subtitle_keywords_config": None, "use_float_render": False,
                   "hdr_vivid": False},
        "canvas_config": {"ratio": "original", "width": width, "height": height,
                          "background": None},
        "tracks": [], "group_container": None,
        "materials": {cat: [] for cat in MATERIAL_CATEGORIES},
        "keyframes": {"videos": [], "audios": [], "texts": [], "stickers": [],
                      "filters": [], "adjusts": [], "handwrites": [], "effects": []},
        "keyframe_graph_list": [], "platform": platform,
        "last_modified_platform": platform, "mutable_config": None, "cover": None,
        "retouch_cover": None, "extra_info": None, "relationships": [],
        "mixed_track_mode_on": False, "render_index_track_mode_on": True,
        "free_render_index_mode_on": False, "static_cover_image_path": "",
        "source": "default", "time_marks": None, "path": "", "lyrics_effects": [],
        "function_assistant_info": None,
        "uneven_animation_template_info": {"composition": "", "content": "", "order": "",
                                           "sub_template_info_list": []},
        "draft_type": "video",
        "smart_ads_info": {"page_from": "", "routine": "", "draft_url": ""},
    }


def repair(draft: dict) -> dict:
    """Normalise a draft before writing: CapCut's own save leaves text segments
    with source_timerange=null, which survives a re-import and wedges the
    exporter. Also recompute the total duration."""
    for t in draft["tracks"]:
        for s in t["segments"]:
            if s.get("source_timerange") is None:
                s["source_timerange"] = {"start": 0,
                                         "duration": s["target_timerange"]["duration"]}
    draft["duration"] = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                             for t in draft["tracks"] for s in t["segments"]), default=0)
    return draft


def check(draft: dict) -> list[str]:
    """Structural problems that would make CapCut drop or mis-render content."""
    problems = []
    ids = {m["id"] for cat in draft["materials"].values() for m in cat
           if isinstance(m, dict) and "id" in m}
    for t in draft["tracks"]:
        for i, s in enumerate(t["segments"]):
            where = f"{t['type']} track segment {i}"
            if s["material_id"] not in ids:
                problems.append(f"{where}: material {s['material_id']} missing")
            missing = [r for r in s.get("extra_material_refs", []) if r not in ids]
            if missing:
                problems.append(f"{where}: {len(missing)} dangling extra refs")
            if s["target_timerange"]["duration"] <= 0:
                problems.append(f"{where}: non-positive duration")
    return problems
