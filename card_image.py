    ovr_center = (OVR_CENTER[0] + ovr_dx, OVR_CENTER[1] + ovr_dy)
    role_center = (ROLE_CENTER[0] + ovr_dx, ROLE_CENTER[1] + ovr_dy)
    ovr_font = _font(FONT_DISPLAY_BOLD, ovr_size)
    role_font = _font(FONT_LABEL, ROLE_SIZE)
    _draw_centered(draw, ovr_center, str(card_row["ovr"]), ovr_font, GOLD)
    _draw_centered(draw, role_center, role_word, role_font, WHITE)

    # ── Batting hand, bottom of the centre band ────────────────────────
    hand_word = HAND_CARD_WORD.get(_col(card_row, "batting_hand"))
    if hand_word:
        _draw_centered(draw, HAND_CENTER, hand_word, _font(FONT_LABEL, HAND_SIZE), WHITE)

    # ── Playstyle logos (max 2): one sits in the middle, two fill the circles ──
    slots = []
    for slot in (1, 2):
        logo = _load_logo(_col(card_row, f"playstyle{slot}"))
        if logo is not None:
            slots.append((slot, logo))
    if len(slots) == 2:
        centers = {1: LOGO_CENTER_1, 2: LOGO_CENTER_2}
    else:
        centers = {s: LOGO_CENTER_SINGLE for s, _ in slots}
    for slot, logo in slots:
        _draw_logo(
            canvas, logo, centers[slot],
            _col(card_row, f"logo{slot}_dx", 0),
            _col(card_row, f"logo{slot}_dy", 0),
            _col(card_row, f"logo{slot}_scale", 100),
        )

    # ── Country (emoji glyphs don't render via truetype fonts in Pillow,
    # so we draw the country name; the emoji is still stored in the DB and
    # used wherever Discord itself renders text, e.g. in embeds). ───────
    country_center = (COUNTRY_CENTER[0], COUNTRY_CENTER[1] + country_dy)
    country_font = _font(FONT_LABEL, country_size)
    _draw_centered(draw, country_center, card_row["country"].upper(), country_font, WHITE)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
