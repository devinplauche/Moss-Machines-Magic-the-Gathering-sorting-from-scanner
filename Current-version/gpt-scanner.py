import cv2
import numpy as np
import requests

# --- CONFIG ---
TOP_STRIP_RATIO = 0.15
DB = {}

# ---------------------------
# CARD DETECTION
# ---------------------------
def detect_card(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5,5), 0)

    edges = cv2.Canny(blur, 50, 150)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)

        if len(approx) == 4:
            return approx

    return None


# ---------------------------
# PERSPECTIVE CORRECTION
# ---------------------------
def warp_card(image, pts):

    pts = pts.reshape(4,2)
    rect = np.zeros((4,2), dtype="float32")

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    (tl, tr, br, bl) = rect

    widthA = np.linalg.norm(br - bl)
    widthB = np.linalg.norm(tr - tl)
    maxWidth = max(int(widthA), int(widthB))

    heightA = np.linalg.norm(tr - br)
    heightB = np.linalg.norm(tl - bl)
    maxHeight = max(int(heightA), int(heightB))

    dst = np.array([
    [0,0],
    [maxWidth-1,0],
    [maxWidth-1,maxHeight-1],
    [0,maxHeight-1]], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))

    return warped


# ---------------------------
# CROP MANA REGION
# ---------------------------
def crop_top_strip(card):

    h = card.shape[0]
    top = int(h * TOP_STRIP_RATIO)

    return card[0:top, :]


# ---------------------------
# FEATURE EXTRACTION
# ---------------------------
def extract_features(region):

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    small = cv2.resize(gray, (32,8))

    return small.flatten()


# ---------------------------
# MATCHING
# ---------------------------
def find_best_match(feature):

    best = None
    best_score = float("inf")

    for name, db_feat in DB.items():

        score = np.linalg.norm(feature - db_feat)

    if score < best_score:
        best_score = score
        best = name

    return best, best_score


# ---------------------------
# SCRYFALL LOOKUP
# ---------------------------
def search_scryfall(name):

    url = f"https://api.scryfall.com/cards/named?fuzzy={name}"

    r = requests.get(url)

    if r.status_code == 200:
        return r.json()["name"]

    return None


# ---------------------------
# MAIN PIPELINE
# ---------------------------
def identify_card(image_path):

    img = cv2.imread(image_path)

    card_contour = detect_card(img)

    if card_contour is None:
        print("No card detected")
        return

    card = warp_card(img, card_contour)

    region = crop_top_strip(card)

    feat = extract_features(region)

    match, score = find_best_match(feat)

    print("Match:", match, "Score:", score)


# Example
identify_card("card_photo.jpg")