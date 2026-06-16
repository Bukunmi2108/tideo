"""Payload schema per event type 

    event_type            when                                   payload
    ------------------    -----------------------------------    ------------------------------------------
    job.created           user commits POST /transcode           {presets: [str], subtitles: bool,
                                                                   source_duration: float}
    job.started           worker begins the job                  {renditions: [str]}
    rendition.started     one rendition begins                   {preset: str}
    rendition.completed   one rendition finishes                 {preset: str, output_bytes: int,
                                                                   encode_seconds: float}
    rendition.failed      a rendition exhausts retries (P4/P6)    {preset: str, error_code: str}
    job.completed         all renditions done, playlist written  {renditions: int, output_bytes_total: int}
    job.failed            the job fails as a whole               {error_code: str, stage: str}
    job.cancelled         user cancels (Phase 6)                 {}
    job.expired           retention sweep expires it (Phase 7)   {}
"""
