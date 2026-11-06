"""Field categories and the removal policy.

Every metadata field a handler reports is assigned exactly one category. The
policy is the set of categories to remove. Some categories are *protected*:
they are needed to display the file correctly (orientation, colour profile,
rotation, language) or they are provenance/disclosure records that this
privacy tool does not strip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Removed by default: fields that identify a person, place or device.
DEFAULT = ("location", "device", "author", "people", "comment", "identifier", "preview", "custom")
# Removed only on request.
OPTIONAL = ("ai", "software", "timestamp", "descriptive", "rights", "capture", "other")
# Never removed by this tool.
PROTECTED = ("technical", "disclosure", "provenance")
ALL_REMOVABLE = DEFAULT + OPTIONAL
CATEGORIES = ALL_REMOVABLE + PROTECTED

DESCRIPTIONS = {
    "location": "GPS coordinates and place names",
    "device": "camera/phone make, model, serial numbers, maker notes",
    "author": "author, artist, creator, owner, last-modified-by, company",
    "people": "face regions, face IDs and people tags (names of people in the photo)",
    "comment": "free-text comments, descriptions, captions",
    "identifier": "unique document/instance IDs, revision counters, editing history",
    "preview": "embedded thumbnails/previews (may show pre-edit content)",
    "custom": "custom document properties",
    "ai": "AI generation parameters: prompts, seeds, workflows, model names",
    "software": "creating/editing software and encoder tags",
    "timestamp": "creation/modification dates",
    "descriptive": "title, subject, keywords, album, genre",
    "rights": "copyright and licensing statements",
    "capture": "exposure and lens settings",
    "other": "fields this tool cannot classify",
    "technical": "needed for correct display/playback (protected)",
    "disclosure": "AI-disclosure labels such as IPTC DigitalSourceType (protected)",
    "provenance": "signed provenance / Content Credentials (protected)",
}


@dataclass(frozen=True)
class Policy:
    remove: frozenset[str]

    @classmethod
    def build(cls, remove_ai: bool = False, also: list[str] | None = None, everything: bool = False) -> "Policy":
        cats = set(ALL_REMOVABLE if everything else DEFAULT)
        if remove_ai:
            cats |= {"ai", "software"}
        for c in also or []:
            for part in c.split(","):
                part = part.strip()
                if not part:
                    continue
                if part in PROTECTED:
                    raise ValueError(f"category '{part}' is protected and cannot be removed")
                if part not in ALL_REMOVABLE:
                    raise ValueError(f"unknown category '{part}' (choose from: {', '.join(ALL_REMOVABLE)})")
                cats.add(part)
        return cls(frozenset(cats))

    def targets(self, category: str) -> bool:
        return category in self.remove

    def to_dict(self) -> dict[str, object]:
        return {"remove": sorted(self.remove)}


# --------------------------------------------------------------------------
# Content sniffing: free-text fields that actually hold generation parameters
# --------------------------------------------------------------------------

_AI_VALUE = re.compile(
    r"(Negative prompt:|\bSteps: \d+|\bSampler: |\bCFG scale: |\bSeed: \d+|\"class_type\"\s*:|KSampler|"
    r"\"sampler_name\"|\"cfg\"\s*:|\bModel hash: |sd-metadata|invokeai)",
    re.I,
)


def looks_like_ai(value: str) -> bool:
    return bool(value) and bool(_AI_VALUE.search(value[:20000]))


_AI_KEY = re.compile(
    r"^(parameters|prompt|negative_?prompt|workflow|seed|sd-metadata|invokeai_metadata|invokeai_graph|dream|"
    r"generation_?data|generation_?parameters|sampler|cfg_?scale|model_?hash|ai_?.*|aiprompt.*|aisystem.*|"
    r"comfy.*|fooocus.*|lora.*|steps|scheduler|denoise.*)$",
    re.I,
)


def is_ai_key(name: str) -> bool:
    return bool(_AI_KEY.match(name))


# --------------------------------------------------------------------------
# ExifTool tags (images and read-only inspection)
# --------------------------------------------------------------------------

_IMG_TECHNICAL = {
    "orientation", "colorspace", "exifversion", "flashpixversion", "componentsconfiguration",
    "ycbcrpositioning", "ycbcrsubsampling", "ycbcrcoefficients", "xresolution", "yresolution",
    "resolutionunit", "imagewidth", "imageheight", "exifimagewidth", "exifimageheight", "bitspersample",
    "compression", "photometricinterpretation", "samplesperpixel", "planarconfiguration",
    "interopindex", "interopversion", "gamma", "whitepoint", "primarychromaticities", "srgbrendering",
    "pixelsperunitx", "pixelsperunity", "pixelunits", "transferfunction", "referenceblackwhite",
    "applicationrecordversion", "codedcharacterset", "envelonerecordversion", "currentiptcdigest",
    "iptcdigest", "xmptoolkit_ignore", "mpfversion", "numberofimages", "mpimageflags", "mpimageformat",
    "mpimagetype", "mpimagelength", "mpimagestart", "dependentimage1entrynumber",
    "dependentimage2entrynumber", "imageuidlist", "totalframes", "colorprofile", "profilename",
    "nativedigest", "hasextendedxmp", "digitalsourcefiletype",
}

_IMG_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("disclosure", re.compile(r"^digitalsourcetype$")),
    ("ai", re.compile(r"^(aipromptinformation|aisystemused|aisystemversionused|parameters|prompt|workflow)$")),
    ("author", re.compile(r"^(aipromptwritername)$")),
    ("location", re.compile(r"gps|^(city|state|province.?state|country|countrycode|country.?primary.?location.*|"
                            r"sub.?location|location.*|locationcreated.*|locationshown.*|"
                            r"countryname|provincestate|worldregion)$")),
    ("device", re.compile(r"^(make|model|serialnumber|internalserialnumber|bodyserialnumber|cameraserialnumber|"
                          r"lensserialnumber|lensmake|lensmodel|lensinfo|lens|lensid|ownername|cameraownername|"
                          r"hostcomputer|uniquecameramodel|localizedcameramodel|cameraid|deviceid|"
                          r"devicesettingdescription|shuttercount|imagecount|firmware.*)$")),
    ("author", re.compile(r"^(artist|xpauthor|author|creator|by-?line|by-?linetitle|credit|writer-?editor|"
                          r"captionwriter|authorsposition|creatorcontactinfo.*|creator(address|city|country|"
                          r"postalcode|region|workemail|worktelephone|workurl).*|contributor|owner|publisher|"
                          r"source|imagecreator.*|copyrightowner.*|licensor.*)$")),
    ("comment", re.compile(r"^(comment|usercomment|xpcomment|xpsubject|imagedescription|description|"
                           r"caption-?abstract|instructions|specialinstructions|notes|extdescraccessibility|"
                           r"altTextaccessibility)$", re.I)),
    ("identifier", re.compile(r"^(imageuniqueid|documentid|instanceid|originaldocumentid|preservedfilename|"
                              r"rawfilename|originalrawfilename|derivedfrom.*|history.*|ingredients.*|"
                              r"manifest.*|pantry.*|versions.*|renditionof.*|jobref.*|imagenumber|"
                              r"uniquedocumentid|transmissionreference|jobid|originaltransmissionreference)$")),
    ("preview", re.compile(r"^(thumbnail.*|preview.*|jpgfromraw|otherimage.*|photoshopthumbnail)$")),
    ("software", re.compile(r"^(software|creatortool|processingsoftware|xmptoolkit|originatingprogram|"
                            r"programversion|historysoftwareagent)$")),
    ("timestamp", re.compile(r"^(datetime.*|.*date|datecreated|subsectime.*|offsettime.*|timecreated|"
                             r"digitalcreationtime|creationtime|releasetime|expirationtime|timezoneoffset)$")),
    ("rights", re.compile(r"^(copyright|copyrightnotice|rights|usageterms|webstatement|marked|"
                          r"copyrightflag|copyrightstatus)$")),
    ("descriptive", re.compile(r"^(title|objectname|headline|keywords|subject|rating|ratingpercent|label|"
                               r"category|supplementalcategories|xptitle|xpkeywords|documentname|pagename|"
                               r"event|scene|subjectcode|intellectualgenre|urgency)$")),
]

PEOPLE_GROUPS = {"XMP-mwg-rs", "XMP-MP", "XMP-MPRI", "XMP-MPReg", "XMP-acdsee-rs"}

_IGNORED_GROUPS0 = {"ExifTool", "System", "Composite", "File", "ICC_Profile", "JFIF", "MPF", "RIFF",
                    "PNG", "APP14", "Photoshop", "Trailer"}


def classify_exiftool(group0: str, group1: str, tag: str, value: str) -> str | None:
    """Return the category for an ExifTool tag, or None to ignore it.

    ``File:Comment`` (the JPEG COM segment) is the only File-group tag that
    is real metadata; ``PNG`` text chunks are classified separately from the
    raw chunk parser so keywords are reported exactly.
    """
    t = tag.lower()
    if not group0:
        return None  # "SourceFile" (the path itself) and other ungrouped keys
    if group0 == "File" and t == "comment":
        return "ai" if looks_like_ai(value) else "comment"
    if group0 == "MakerNotes":
        return "device"
    if group0 in ("JUMBF", "C2PA"):
        return "provenance"
    if group1 in PEOPLE_GROUPS or t.startswith(("personinimage", "regionpersondisplayname", "regionname")):
        return "people"
    if group0 == "APP10" or (group0 == "JPEG" and group1 == "APP2"):
        return "technical"  # Apple HDR gain curve / ISO 21496 gain-map URN, needed for HDR display
    if group0 == "Photoshop":
        # Photoshop IRB: thumbnails are previews, the rest is mostly technical.
        return "preview" if "thumbnail" in t else None
    if group0 in _IGNORED_GROUPS0:
        return None
    if group1 == "IFD1":
        return "preview"
    if t in _IMG_TECHNICAL:
        return "technical"
    for cat, rx in _IMG_RULES:
        if rx.search(t):
            if cat in ("comment", "descriptive", "other") and looks_like_ai(value):
                return "ai"
            return cat
    if group1 == "GPS":
        return "location"
    if group1 == "ExifIFD":
        return "capture"
    if group1 in ("XMP-xmpMM",):
        return "identifier"
    if group0 == "ICC_Profile" or group1.startswith("ICC"):
        return "technical"
    return "ai" if looks_like_ai(value) else "other"


# --------------------------------------------------------------------------
# PNG text chunks (keyword exactly as stored)
# --------------------------------------------------------------------------

_PNG_KEY = {
    "author": "author", "artist": "author", "copyright": "rights", "disclaimer": "rights",
    "comment": "comment", "description": "comment", "warning": "comment",
    "title": "descriptive", "source": "device", "software": "software",
    "creation time": "timestamp", "creationtime": "timestamp",
    "label": "descriptive", "keywords": "descriptive",
}


def classify_png_text(keyword: str, value: str) -> str:
    k = keyword.strip().lower()
    if is_ai_key(k):
        return "ai"
    cat = _PNG_KEY.get(k)
    if cat in ("comment", "descriptive", None) and looks_like_ai(value):
        return "ai"
    if cat:
        return cat
    if "gps" in k or "location" in k:
        return "location"
    return "other"


# --------------------------------------------------------------------------
# FFmpeg / ffprobe container and stream tags
# --------------------------------------------------------------------------

AV_STRUCTURAL = {
    # Written or managed by the muxer; never re-added explicitly.
    "major_brand", "minor_version", "compatible_brands", "duration", "bps", "number_of_frames",
    "number_of_bytes", "vendor_id", "_statistics_tags", "_statistics_writing_app", "_statistics_writing_date_utc",
}

_AV_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("technical", re.compile(r"^(major_brand|minor_version|compatible_brands|language|handler_name|vendor_id|"
                             r"duration|bps|number_of_frames|number_of_bytes|_statistics_.*|rotate|timecode|"
                             r"mimetype|filename|reel_name|com\.apple\.quicktime\.(full-frame-rate-playback-"
                             r"intent|live-photo.*|camera\.(focal_length.*|lens_model_ignore))|"
                             r"com\.android\.(capture\.fps|version))$")),
    ("location", re.compile(r"(^|\.)(location|xyz|gps.*|location\..*|iso6709)($|\.)|^location-")),
    ("device", re.compile(r"(^|\.)(make|model|manufacturer|serial.*|camera.*|lens.*|firmware|device.*)$")),
    ("ai", re.compile(r"^(prompt|parameters|workflow|seed|negative_?prompt|ai_.*|generator|"
                      r"com\.[a-z]+\.ai\..*)$")),
    ("author", re.compile(r"(^|\.)(artist|album_artist|author|composer|performer|lyricist|conductor|"
                          r"encoded_by|encodedby|owner|director|producer|publisher|arranger|writer|"
                          r"original_artist|remixed_by)$")),
    ("comment", re.compile(r"(^|\.)(comment|description|synopsis|lyrics.*|notes|information)$")),
    ("software", re.compile(r"(^|\.)(encoder|software|encoding_tool|encoder_settings|writing_application|tool)$")),
    ("timestamp", re.compile(r"(^|\.)(creation_time|date|creationdate|date_recorded|date_released|year|"
                             r"originaldate|tdor|tdrc)$")),
    ("identifier", re.compile(r"(^|\.)(content\.identifier|uuid|uid|isrc|barcode|catalog.*|"
                              r"musicbrainz.*|acoustid.*|itunes.*id|asin)$")),
    ("rights", re.compile(r"(^|\.)(copyright|license|rights|terms_of_use)$")),
    ("descriptive", re.compile(r"(^|\.)(title|album|genre|track|tracktotal|totaltracks|disc|disctotal|"
                               r"totaldiscs|show|episode_id|episode_sort|season_number|network|grouping|"
                               r"compilation|.*sort|rating|keywords|media_type|gapless_playback|hd_video|"
                               r"bpm|tbpm|mood|key|subtitle|part_number)$")),
]


def classify_av(key: str, value: str) -> str:
    k = key.lower()
    if k.startswith("com.apple.quicktime."):
        rest = k[len("com.apple.quicktime."):]
    else:
        rest = k
    # Strip ISO-639 language suffix used by MP4 ("location-eng").
    base = re.sub(r"-[a-z]{3}$", "", rest)
    for cat, rx in _AV_RULES:
        if rx.search(base) or rx.search(k):
            if cat in ("comment", "descriptive") and looks_like_ai(value):
                return "ai"
            return cat
    return "ai" if looks_like_ai(value) else "other"
