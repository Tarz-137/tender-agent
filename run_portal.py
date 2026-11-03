import subprocess
import sys

# portals = ["assam",
#             "chandigarh",
#             "goa",
#             "haryana",
#             "himachal_pradesh",
#             "jammu_kashmir",
#             "jharkhand",
#             "kerala",
#             "madhya_pradesh",
#             "maharashtra",
#             "new_delhi",
#             "odisha",
#             "punjab",
#             "rajasthan",
#             "tamil_nadu",
#             "uttar_pradesh",
#             "uttarakhand",
#             "west_bengal"]
portals = [
            "uttarakhand"]


for portal in portals:
    print(f"Running portal: {portal}")

    result = subprocess.run(
        [sys.executable,"main.py","--portal",portal],
        check= False
    )

    if result.returncode == 0:
        print(f"{portal} completed")
    else:
        print(f"{portal} failed with code {result.returncode}")

