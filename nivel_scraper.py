# nivel_scraper.py
import os
import asyncio
import pandas as pd
from PIL import Image
from playwright.async_api import async_playwright

USERNAME = "ryde"
PASSWORD = "634538"
CSV_FILE = "feilparkeringer.csv"

async def download_csv(page):
    async with page.expect_download() as download_info:
        await page.locator('button:has-text("CSV")').click()
    download = await download_info.value
    await download.save_as(CSV_FILE)

def delete_images_for_fixed_reports(reports):
    """
    Deletes image files related to reports with status 'fixed'.
    
    Args:
        reports (list): List of report dictionaries with keys 'status', 'image', and optionally other image paths.
    """
    for report in reports:
        if report.get("status") == "fixed":
            for key in ["image", "map_image", "photo_image"]:
                path = report.get(key)
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        print(f"🗑️ Deleted {path}")
                    except Exception as e:
                        print(f"⚠️ Could not delete {path}: {e}")

async def scrape_new_status_reports_with_images():
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)  # headless = True to avoid UI blocks
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        # Login
        await page.goto("https://manager.reporting.nivel.no/#/reports")
        await page.get_by_label("Brukernavn").fill(USERNAME)
        await page.get_by_label("Passord").fill(PASSWORD)
        await page.get_by_role("button", name="Logg inn").click()
        await page.wait_for_selector("text=Feilparkeringer", timeout=15000)

        # Choose Bergen
        await page.locator('button:has(svg)').first.click()
        await page.wait_for_timeout(500)
        await page.locator("div[role='button']").nth(0).click()
        await page.get_by_text("Bergen").click()
        await page.wait_for_timeout(2000)

        # Download and filter
        await download_csv(page)
        df = pd.read_csv(CSV_FILE)
        new_reports = df[df["status"] == "new"]
        print(f"🆕 Found {len(new_reports)} reports with status 'new'.")

        for _, row in new_reports.iterrows():
            vehicle_id = row["vehicleId"]
            if vehicle_id == 0 or pd.isna(vehicle_id):
                print("⚠️ Vehicle ID is NaN, skipping...")
                Vehicle_id = 0
                continue
            vehicle_id = str(int(vehicle_id))
            lat = row["positionLat"]
            lng = row["positionLng"]

            try:
                report_id = str(int(row["reportId"]))
                lat = row["positionLat"]
                lng = row["positionLng"]
                detail_url = f"https://manager.reporting.nivel.no/#/reports/{report_id}"

                await page.goto(detail_url)
                await page.wait_for_timeout(2000)

                # Screenshot full view
                full_path = f"screenshot_{report_id}.png"
                await page.screenshot(path=full_path, full_page=False)

              # Open and crop slightly from the right to remove gray margins
                img = Image.open(full_path)
                width, height = img.size

                # Crop the image to remove the right margin
                cropped_img = img.crop((0, 0, width, height))
                final_path = f"report_{report_id}.jpg"
                cropped_img.save(final_path, format="JPEG", quality=95)

                # Try to extract description text
                try:
                    raw_description = await page.locator("div:below(:text('Registrert av'))").nth(0).inner_text()
                    description = raw_description.strip()
                except:
                    description = ""  # fallback if there's no text

                if description == "FIKSET\nAVVIS\nSKAL GJØRE":
                    description = ""

                results.append({
                    "id": report_id,
                    "qr_code": vehicle_id,
                    "status": row["status"],
                    "description": description,
                    "lat": lat,
                    "lng": lng,
                    "image": final_path
                })

            except Exception as e:
                print(f"❌ Failed to process report {report_id}: {e}")

            finally:
                try:
                    await page.locator("button:has(svg):right-of(h1)").first.click(timeout=3000)
                except:
                    try:
                        await page.keyboard.press("Escape")
                    except:
                        print("⚠️ Could not close the detail view manually.")
                await page.wait_for_timeout(800)
        

        await browser.close()

    return results




if __name__ == "__main__":
    data = asyncio.run(scrape_new_status_reports_with_images())
    for d in data:
        print(d)