from pyrogram import Client, filters
from info import CHANNELS
from database.ia_filterdb import save_file
from util.thumbnail_branding import brand_and_archive_message

media_filter = filters.document | filters.video | filters.audio


@Client.on_message(filters.chat(CHANNELS) & media_filter)
async def media(bot, message):
    """Media Handler"""
    media = await brand_and_archive_message(bot, message)
    if media is None:
        return
    await save_file(bot, media)
  
