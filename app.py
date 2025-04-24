import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from dotenv import load_dotenv
import os
import json
import asyncio

from nivel_scraper import scrape_new_status_reports_with_images
from playwright.async_api import async_playwright

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
USERNAME = "ryde"
PASSWORD = "634538"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

MESSAGE_TRACK_FILE = "messages.json"

def delete_images_for_fixed_reports(report):
    for key in ["image", "map_image", "photo_image"]:
        path = report.get(key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
                print(f"🗑️ Deleted {path}")
            except Exception as e:
                print(f"⚠️ Could not delete {path}: {e}")

# Load or initialize message track file
if not os.path.exists(MESSAGE_TRACK_FILE):
    with open(MESSAGE_TRACK_FILE, "w") as f:
        json.dump({}, f)

def load_message_ids():
    with open(MESSAGE_TRACK_FILE, "r") as f:
        return json.load(f)

def save_message_ids(data):
    with open(MESSAGE_TRACK_FILE, "w") as f:
        json.dump(data, f)

# Press button on Nivel site
async def press_nivel_action(report_id, action):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto("https://manager.reporting.nivel.no/#/reports")

            # Wait for login
            await page.get_by_label("Brukernavn").fill(USERNAME)
            await page.get_by_label("Passord").fill(PASSWORD)
            await page.get_by_role("button", name="Logg inn").click()

            # Wait until Feilparkeringer appears
            await page.wait_for_selector("text=Feilparkeringer", timeout=15000)

            # Open menu and choose Bergen
            await page.locator('button:has(svg)').first.click()
            await page.wait_for_timeout(500)
            await page.locator("div[role='button']").nth(0).click()
            await page.get_by_text("Bergen").click()
            await page.wait_for_timeout(1500)  # Let the view load

            # THEN navigate directly to the report
            await page.goto(f"https://manager.reporting.nivel.no/#/reports/{report_id}")
            await page.wait_for_timeout(2000)
            await page.locator(f'button:has-text("{action}")').click()
            await page.wait_for_timeout(1000)


            # Confirm dialog
            if action == "Skal Gjøre":
                await page.locator('button:has-text("REGISTRER")').click()
                await page.wait_for_timeout(1000)
            elif action == "Avvis":
                await page.locator('button:has-text("AVVIS OPPGAVE")').click()
                await page.wait_for_timeout(1000)
            elif action == "Fikset":
                await page.locator('button:has-text("REGISTRER")').click()
                await page.wait_for_timeout(1000)

            print(f"✅ '{action}' pressed on report {report_id}")
            return True

        except Exception as e:
            print(f"❌ Failed to press {action} on report {report_id}: {e}")
            return False
        finally:
            await browser.close()

# Discord UI
class ReportView(View):
    def __init__(self, report_id, status, report_data=None):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.report_data = report_data or {}

        if status == "new":
            self.add_item(ReportButton("Fikset", report_id, self))
            self.add_item(ReportButton("Avvis", report_id, self))
            self.add_item(ReportButton("Skal Gjøre", report_id, self))
        elif status == "ongoing":
            self.add_item(ReportButton("Fikset", report_id, self))

class ReportButton(Button):
    def __init__(self, label, report_id, parent_view, disabled=False):
        super().__init__(label=label, style=discord.ButtonStyle.primary, disabled=disabled)
        self.report_id = report_id
        self.label_text = label
        self.parent_view = parent_view  # 👈 viktig: referanse til View som har report_data

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        success = await press_nivel_action(self.report_id, self.label_text)

        if success:
            if self.label_text == "Fikset":
                await interaction.message.delete()
                print(f"🗑️ Deleted message for report {self.report_id}")
                delete_images_for_fixed_reports(self.parent_view.report_data)

            else:
                for item in self.parent_view.children:
                    item.disabled = True

                if self.label_text == "Skal Gjøre":
                    new_view = ReportView(report_id=self.report_id, status="ongoing", report_data=self.parent_view.report_data)
                    await interaction.message.edit(view=new_view)
                else:
                    await interaction.message.edit(view=self.parent_view)

            await interaction.followup.send(
                f"🔧 Rapport {self.report_id} ble oppdatert: {self.label_text}",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ Noe gikk galt. Kunne ikke trykke '{self.label_text}' på rapport {self.report_id}",
                ephemeral=True
            )

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    check_for_reports.start()

@tasks.loop(minutes=1)
async def check_for_reports():
    print("🔁 Checking for new reports...")
    reports = await scrape_new_status_reports_with_images()
    sent_messages = load_message_ids()
    channel = bot.get_channel(CHANNEL_ID)

    if not reports:
        print("✅ No new reports found.")
        return

    for report in reports:
        report_id = str(report["id"])

        # Already sent but now changed to rejected or fixed → delete
        if report_id in sent_messages and report["status"] in ["rejected", "fixed"]:
            try:
                msg = await channel.fetch_message(sent_messages[report_id])
                await msg.delete()
                print(f"🗑️ Deleted message for report {report_id}")
                del sent_messages[report_id]
                save_message_ids(sent_messages)
            except:
                pass
            continue

        # Already sent and still valid
        if report_id in sent_messages:
            continue

        # Send new report
        embed = discord.Embed(
            title=f"Feilparkering QR: {report['qr_code']}",
            color=discord.Color.red()
        )
        if report["description"]:
            embed.description = report["description"]

        embed.set_image(url="attachment://photo.png")
        file = discord.File(report["image"], filename="photo.png")

        view = ReportView(report_id=report["id"], status=report["status"], report_data=report)
        mention = "<@&1331912186095992864>"  # Replace this with the actual Nivel role ID

        message = await channel.send(content=mention, embed=embed, file=file, view=view)
        sent_messages[report_id] = message.id  # ✅ Only save the message ID (not the whole object!)
        save_message_ids(sent_messages)
        print(f"📤 Sent report {report_id}")

bot.run(DISCORD_TOKEN)