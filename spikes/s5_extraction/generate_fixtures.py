"""Generate 20 diverse HTML fixtures with gold text for Spike S5 and Step 8."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

PAGES = [
    (
        "01_news_article",
        """<!DOCTYPE html><html><head><title>Spacecraft Lands on Asteroid</title></head><body>
<header><nav><a href="/">Home</a><a href="/news">News</a></nav></header>
<div class="cookie-banner"><p>We use cookies to improve your experience. Accept?</p></div>
<article>
<h1>Spacecraft Successfully Touches Down on Remote Asteroid</h1>
<p class="byline">By Sarah Jenkins | Published November 10, 2026</p>
<p>An autonomous exploration probe touched down on the carbon-rich asteroid early Tuesday morning after a journey spanning four billion miles through the inner solar system.</p>
<p>Scientists at mission control confirmed signal acquisition at 04:12 UTC. Initial telemetry indicates all onboard scientific instruments are operating normally and structural integrity remains at one hundred percent.</p>
<p>The robotic lander will collect core samples and analyze subsurface ice deposits over the next six months before attempting a return trajectory to Earth in late 2028.</p>
</article>
<aside class="advertisement"><p>Special Discount on Telescopes! Buy now.</p></aside>
<footer><p>&copy; 2026 Global Science News. All rights reserved.</p></footer>
</body></html>""",
        """Spacecraft Successfully Touches Down on Remote Asteroid
An autonomous exploration probe touched down on the carbon-rich asteroid early Tuesday morning after a journey spanning four billion miles through the inner solar system.
Scientists at mission control confirmed signal acquisition at 04:12 UTC. Initial telemetry indicates all onboard scientific instruments are operating normally and structural integrity remains at one hundred percent.
The robotic lander will collect core samples and analyze subsurface ice deposits over the next six months before attempting a return trajectory to Earth in late 2028.""",
    ),
    (
        "02_documentation",
        """<!DOCTYPE html><html><head><title>Database Connection Pooling Guide</title></head><body>
<nav class="sidebar"><ul><li>Intro</li><li>Setup</li><li>Config</li></ul></nav>
<main>
<h1>Database Connection Pooling</h1>
<p>Connection pooling minimizes the overhead of repeatedly opening and closing database sockets in high-concurrency environments.</p>
<h2>Recommended Pool Sizing</h2>
<p>For most read-heavy workloads, sizing the connection pool to match the number of physical CPU cores plus effective disk spindles provides optimal throughput without thread contention.</p>
<p>Ensure that connection timeouts are configured between five and ten seconds to prevent query starvation during sudden traffic spikes.</p>
</main>
<footer>Documentation version 2.4.0</footer>
</body></html>""",
        """Database Connection Pooling
Connection pooling minimizes the overhead of repeatedly opening and closing database sockets in high-concurrency environments.
Recommended Pool Sizing
For most read-heavy workloads, sizing the connection pool to match the number of physical CPU cores plus effective disk spindles provides optimal throughput without thread contention.
Ensure that connection timeouts are configured between five and ten seconds to prevent query starvation during sudden traffic spikes.""",
    ),
    (
        "03_blog_post",
        """<!DOCTYPE html><html><head><title>Why We Rewrote Our Pipeline in Python</title></head><body>
<header><p>The Engineering Chronicles</p></header>
<article>
<h1>Why We Rewrote Our Pipeline in Python</h1>
<p>Over the past three years our data processing pipeline grew increasingly rigid and difficult to maintain across distributed clusters.</p>
<p>Migrating to modern Python 3.12 with async workers allowed us to eliminate thousands of lines of boilerplate code while drastically accelerating development velocity.</p>
<p>Our benchmark measurements revealed that overall throughput actually improved by fifteen percent due to streamlined serialization and reduced memory fragmentation.</p>
</article>
<div class="share-buttons"><button>Share on Twitter</button></div>
<footer>Published by Engineering Team</footer>
</body></html>""",
        """Why We Rewrote Our Pipeline in Python
Over the past three years our data processing pipeline grew increasingly rigid and difficult to maintain across distributed clusters.
Migrating to modern Python 3.12 with async workers allowed us to eliminate thousands of lines of boilerplate code while drastically accelerating development velocity.
Our benchmark measurements revealed that overall throughput actually improved by fifteen percent due to streamlined serialization and reduced memory fragmentation.""",
    ),
    (
        "04_forum_thread",
        """<!DOCTYPE html><html><head><title>Optimal SSD filesystem options</title></head><body>
<nav><a href="/forum">Forums</a> &gt; Hardware</nav>
<main>
<h1>Optimal SSD filesystem options for SQLite WAL</h1>
<div class="post">
<p>When running intensive SQLite write transactions with write-ahead logging enabled on modern NVMe drives, filesystem selection plays a measurable role in durability guarantees.</p>
<p>Using standard NTFS or ext4 with default allocation unit sizes provides the most consistent fsync performance and avoids excessive write amplification.</p>
<p>Ensure that directory syncing is avoided when not supported by the underlying platform to prevent unexpected permission errors or latency spikes.</p>
</div>
</main>
<footer>Community Board 2026</footer>
</body></html>""",
        """Optimal SSD filesystem options for SQLite WAL
When running intensive SQLite write transactions with write-ahead logging enabled on modern NVMe drives, filesystem selection plays a measurable role in durability guarantees.
Using standard NTFS or ext4 with default allocation unit sizes provides the most consistent fsync performance and avoids excessive write amplification.
Ensure that directory syncing is avoided when not supported by the underlying platform to prevent unexpected permission errors or latency spikes.""",
    ),
    (
        "05_product_page",
        """<!DOCTYPE html><html><head><title>Precision Barometer Pro</title></head><body>
<header>Shop Nav</header>
<main>
<h1>Precision Barometer Pro</h1>
<p class="price">$149.99</p>
<div class="description">
<p>The Precision Barometer Pro delivers medical-grade atmospheric pressure sensing with calibrated accuracy to within 0.05 hectopascals.</p>
<p>Constructed from anodized aircraft aluminum, this device features internal temperature compensation and USB-C connectivity for automated data logging.</p>
<p>Ideal for meteorological researchers, altitude calibration laboratories, and industrial cleanroom environmental monitoring stations.</p>
</div>
</main>
<div class="related">Related items</div>
<footer>Store footer</footer>
</body></html>""",
        """Precision Barometer Pro
The Precision Barometer Pro delivers medical-grade atmospheric pressure sensing with calibrated accuracy to within 0.05 hectopascals.
Constructed from anodized aircraft aluminum, this device features internal temperature compensation and USB-C connectivity for automated data logging.
Ideal for meteorological researchers, altitude calibration laboratories, and industrial cleanroom environmental monitoring stations.""",
    ),
    (
        "06_wiki_article",
        """<!DOCTYPE html><html><head><title>Atmospheric Optics</title></head><body>
<header>Encyclopedia</header>
<article>
<h1>Atmospheric Optics</h1>
<p>Atmospheric optics examines how electromagnetic radiation interacts with the gases, aerosols, and hydrometeors present in planetary atmospheres.</p>
<h2>Scattering Phenomena</h2>
<p>Rayleigh scattering by molecules much smaller than the wavelength of light accounts for the blue hue of the daytime sky and the vivid red shades observed at twilight.</p>
<p>Mie scattering occurs when particles have dimensions comparable to or larger than the light wavelength, creating optical phenomena such as halos, coronas, and atmospheric glory.</p>
</article>
<footer>Categories: Optics | Atmospheric Science</footer>
</body></html>""",
        """Atmospheric Optics
Atmospheric optics examines how electromagnetic radiation interacts with the gases, aerosols, and hydrometeors present in planetary atmospheres.
Scattering Phenomena
Rayleigh scattering by molecules much smaller than the wavelength of light accounts for the blue hue of the daytime sky and the vivid red shades observed at twilight.
Mie scattering occurs when particles have dimensions comparable to or larger than the light wavelength, creating optical phenomena such as halos, coronas, and atmospheric glory.""",
    ),
    (
        "07_scientific_abstract",
        """<!DOCTYPE html><html><head><title>Analysis of High-Entropy Alloys</title></head><body>
<header>Journal of Materials</header>
<main>
<h1>Microstructural Evolution in High-Entropy Alloys Under High Strain Rates</h1>
<p class="abstract-heading"><strong>Abstract</strong></p>
<p>We investigate the mechanical response and dislocation patterning in face-centered cubic high-entropy alloys subjected to dynamic impact loading at strain rates exceeding 1000 per second.</p>
<p>Transmission electron microscopy revealed widespread nanotwinning accompanied by localized shear banding, contributing to an exceptional combination of tensile strength and fracture toughness.</p>
<p>These findings provide mechanistic guidelines for designing damage-tolerant structural components intended for extreme cryogenic and aerospace environments.</p>
</main>
<footer>DOI: 10.1000/mat.2026.04</footer>
</body></html>""",
        """Microstructural Evolution in High-Entropy Alloys Under High Strain Rates
Abstract
We investigate the mechanical response and dislocation patterning in face-centered cubic high-entropy alloys subjected to dynamic impact loading at strain rates exceeding 1000 per second.
Transmission electron microscopy revealed widespread nanotwinning accompanied by localized shear banding, contributing to an exceptional combination of tensile strength and fracture toughness.
These findings provide mechanistic guidelines for designing damage-tolerant structural components intended for extreme cryogenic and aerospace environments.""",
    ),
    (
        "08_recipe_page",
        """<!DOCTYPE html><html><head><title>Traditional Sourdough Bread Recipe</title></head><body>
<header>Culinary Journal</header>
<article>
<h1>Traditional Artisan Sourdough Bread</h1>
<p>Baking artisan sourdough bread relies entirely on natural wild yeast fermentation and slow enzymatic gluten breakdown.</p>
<h2>Instructions and Fermentation</h2>
<p>Combine unbleached bread flour, filtered water, and active levain at eighty percent hydration, allowing thirty minutes for autolyse before kneading.</p>
<p>Perform four stretch-and-fold sets across three hours of bulk fermentation before shaping and proofing overnight in a refrigerated banneton basket.</p>
<p>Bake inside a preheated cast iron Dutch oven at 230 degrees Celsius with the lid closed to trap essential moisture for blistered crust expansion.</p>
</article>
<footer>Recipe index</footer>
</body></html>""",
        """Traditional Artisan Sourdough Bread
Baking artisan sourdough bread relies entirely on natural wild yeast fermentation and slow enzymatic gluten breakdown.
Instructions and Fermentation
Combine unbleached bread flour, filtered water, and active levain at eighty percent hydration, allowing thirty minutes for autolyse before kneading.
Perform four stretch-and-fold sets across three hours of bulk fermentation before shaping and proofing overnight in a refrigerated banneton basket.
Bake inside a preheated cast iron Dutch oven at 230 degrees Celsius with the lid closed to trap essential moisture for blistered crust expansion.""",
    ),
    (
        "09_faq_page",
        """<!DOCTYPE html><html><head><title>Electric Vehicle Charging FAQ</title></head><body>
<header>Support</header>
<main>
<h1>Electric Vehicle Charging Frequently Asked Questions</h1>
<h2>What is the difference between Level 2 and Level 3 charging?</h2>
<p>Level 2 charging utilizes 240-volt alternating current to replenish forty miles of driving range per hour, making it ideal for residential overnight charging stations.</p>
<p>Level 3 direct current fast chargers supply high-voltage direct current directly to the vehicle battery, achieving eighty percent state of charge in twenty to thirty minutes.</p>
<h2>Does frequent fast charging degrade battery lifespan?</h2>
<p>Modern battery management systems throttle charge rates and regulate cell temperatures to minimize thermal degradation during repeated high-power charging cycles.</p>
</main>
<footer>Help desk</footer>
</body></html>""",
        """Electric Vehicle Charging Frequently Asked Questions
What is the difference between Level 2 and Level 3 charging?
Level 2 charging utilizes 240-volt alternating current to replenish forty miles of driving range per hour, making it ideal for residential overnight charging stations.
Level 3 direct current fast chargers supply high-voltage direct current directly to the vehicle battery, achieving eighty percent state of charge in twenty to thirty minutes.
Does frequent fast charging degrade battery lifespan?
Modern battery management systems throttle charge rates and regulate cell temperatures to minimize thermal degradation during repeated high-power charging cycles.""",
    ),
    (
        "10_press_release",
        """<!DOCTYPE html><html><head><title>Renewable Energy Grid Commissioning</title></head><body>
<header>Company Media Center</header>
<article>
<h1>Apex Clean Energy Commissions 500MW Offshore Wind Installation</h1>
<p class="dateline">SEATTLE &mdash; October 14, 2026</p>
<p>Apex Clean Energy today announced the full commercial operation of its flagship offshore wind facility located thirty miles off the Pacific coastline.</p>
<p>The project comprises sixty-five high-capacity maritime turbines capable of generating over 1.8 terawatt-hours of clean electricity annually for regional utility grids.</p>
<p>Advanced subsea transmission cabling transmits generated power to onshore distribution substations with negligible transmission losses.</p>
</article>
<footer>Contact: press@apexenergy.example</footer>
</body></html>""",
        """Apex Clean Energy Commissions 500MW Offshore Wind Installation
Apex Clean Energy today announced the full commercial operation of its flagship offshore wind facility located thirty miles off the Pacific coastline.
The project comprises sixty-five high-capacity maritime turbines capable of generating over 1.8 terawatt-hours of clean electricity annually for regional utility grids.
Advanced subsea transmission cabling transmits generated power to onshore distribution substations with negligible transmission losses.""",
    ),
    (
        "11_tutorial_guide",
        """<!DOCTYPE html><html><head><title>Configuring Linux Firewall with NFTables</title></head><body>
<header>Sysadmin Guide</header>
<main>
<h1>Configuring Modern Linux Firewalls with NFTables</h1>
<p>NFTables succeeds iptables as the standard packet classification framework within modern Linux kernels, offering unified syntax and enhanced filtering efficiency.</p>
<h2>Step 1: Establishing Default Drop Rules</h2>
<p>Begin by defining an inet table with inbound and forward chains set to drop incoming packets by default to establish a secure perimeter.</p>
<h2>Step 2: Permitting Loopback and Established Sockets</h2>
<p>Explicitly permit loopback traffic and establish stateful inspection rules allowing packets associated with existing and related network connections.</p>
</main>
<footer>Linux tutorial</footer>
</body></html>""",
        """Configuring Modern Linux Firewalls with NFTables
NFTables succeeds iptables as the standard packet classification framework within modern Linux kernels, offering unified syntax and enhanced filtering efficiency.
Step 1: Establishing Default Drop Rules
Begin by defining an inet table with inbound and forward chains set to drop incoming packets by default to establish a secure perimeter.
Step 2: Permitting Loopback and Established Sockets
Explicitly permit loopback traffic and establish stateful inspection rules allowing packets associated with existing and related network connections.""",
    ),
    (
        "12_about_us",
        """<!DOCTYPE html><html><head><title>About Quantum Sensing Institute</title></head><body>
<header>Site Nav</header>
<article>
<h1>About the Quantum Sensing Institute</h1>
<p>The Quantum Sensing Institute is an independent non-profit research foundation dedicated to pioneering atomic gravimetry and quantum optical measurement standards.</p>
<p>Founded in 2018, our multidisciplinary team of physicists, optical engineers, and computer scientists collaborates with international university consortia.</p>
<p>Our open-source instrument blueprints and calibrated datasets are freely accessible to accelerate scientific discovery in geophysics and environmental observation.</p>
</article>
<footer>Footer info</footer>
</body></html>""",
        """About the Quantum Sensing Institute
The Quantum Sensing Institute is an independent non-profit research foundation dedicated to pioneering atomic gravimetry and quantum optical measurement standards.
Founded in 2018, our multidisciplinary team of physicists, optical engineers, and computer scientists collaborates with international university consortia.
Our open-source instrument blueprints and calibrated datasets are freely accessible to accelerate scientific discovery in geophysics and environmental observation.""",
    ),
    (
        "13_event_page",
        """<!DOCTYPE html><html><head><title>International Cryptography Symposium 2026</title></head><body>
<header>Conference Banner</header>
<main>
<h1>International Cryptography Symposium 2026</h1>
<p>The annual symposium convenes academic researchers and security practitioners to discuss advancements in post-quantum cryptography, lattice theory, and zero-knowledge proofs.</p>
<h2>Keynote Sessions and Workshops</h2>
<p>Keynote speakers will address formal verification of cryptographic implementations and fault-injection countermeasures in embedded microcontrollers.</p>
<p>Peer-reviewed technical presentations and hands-on standardization workshops will take place across four conference tracks from August 18 through August 22.</p>
</main>
<footer>Symposium organizers</footer>
</body></html>""",
        """International Cryptography Symposium 2026
The annual symposium convenes academic researchers and security practitioners to discuss advancements in post-quantum cryptography, lattice theory, and zero-knowledge proofs.
Keynote Sessions and Workshops
Keynote speakers will address formal verification of cryptographic implementations and fault-injection countermeasures in embedded microcontrollers.
Peer-reviewed technical presentations and hands-on standardization workshops will take place across four conference tracks from August 18 through August 22.""",
    ),
    (
        "14_privacy_policy",
        """<!DOCTYPE html><html><head><title>Data Protection Policy</title></head><body>
<header>Legal Header</header>
<article>
<h1>Data Protection and Operator Privacy Policy</h1>
<p>This policy details the operational commitments and automated safeguards governing data handling within our offline search engine architecture.</p>
<h2>Zero Cookie and Identifier Guarantee</h2>
<p>Our search engine software operates entirely without tracking cookies, browser storage tokens, device fingerprints, or advertising telemetries.</p>
<p>All network transactions are strictly initiated by operator jobs via parameterized CLI commands and verified against public IP constraints.</p>
</article>
<footer>Legal notice</footer>
</body></html>""",
        """Data Protection and Operator Privacy Policy
This policy details the operational commitments and automated safeguards governing data handling within our offline search engine architecture.
Zero Cookie and Identifier Guarantee
Our search engine software operates entirely without tracking cookies, browser storage tokens, device fingerprints, or advertising telemetries.
All network transactions are strictly initiated by operator jobs via parameterized CLI commands and verified against public IP constraints.""",
    ),
    (
        "15_job_posting",
        """<!DOCTYPE html><html><head><title>Senior Distributed Systems Engineer</title></head><body>
<header>Careers</header>
<main>
<h1>Senior Distributed Systems Engineer</h1>
<p>We are seeking an experienced systems engineer to architect resilient indexing pipelines and high-throughput transaction stores.</p>
<h2>Responsibilities and Role Overview</h2>
<p>You will design fault-tolerant consensus algorithms, optimize storage representations, and implement deterministic simulation testing frameworks.</p>
<p>Ideal candidates demonstrate deep familiarity with modern systems languages, concurrency primitives, and asynchronous operating system interfaces.</p>
</main>
<footer>Apply now</footer>
</body></html>""",
        """Senior Distributed Systems Engineer
We are seeking an experienced systems engineer to architect resilient indexing pipelines and high-throughput transaction stores.
Responsibilities and Role Overview
You will design fault-tolerant consensus algorithms, optimize storage representations, and implement deterministic simulation testing frameworks.
Ideal candidates demonstrate deep familiarity with modern systems languages, concurrency primitives, and asynchronous operating system interfaces.""",
    ),
    (
        "16_minimal_text",
        """<!DOCTYPE html><html><head><title>Minimalist Essay</title></head><body>
<h1>Simplicity as an Architectural Compass</h1>
<p>Complex systems inevitably accumulate hidden failure modes that confound operators and obscure foundational invariants during critical incidents.</p>
<p>By constraining software surface areas and adhering strictly to documented design contracts, engineers preserve maintainability and operational predictability across decades.</p>
<p>True engineering excellence resides not in the accumulation of baroque features, but in the deliberate refinement of indispensable primitives.</p>
</body></html>""",
        """Simplicity as an Architectural Compass
Complex systems inevitably accumulate hidden failure modes that confound operators and obscure foundational invariants during critical incidents.
By constraining software surface areas and adhering strictly to documented design contracts, engineers preserve maintainability and operational predictability across decades.
True engineering excellence resides not in the accumulation of baroque features, but in the deliberate refinement of indispensable primitives.""",
    ),
    (
        "17_table_data",
        """<!DOCTYPE html><html><head><title>Quarterly Hydrological Survey</title></head><body>
<header>Hydrology Dept</header>
<article>
<h1>Quarterly Regional Hydrological Survey Results</h1>
<p>The regional water conservation board recorded reservoir storage volumes and watershed runoff coefficients across three river basins.</p>
<p>Alpine snowpack water equivalents measured twenty percent above historical averages, ensuring consistent summer meltwater replenishment for municipal distribution.</p>
<p>Monitoring stations recorded stable groundwater aquifer levels with negligible saline intrusion along vulnerable coastal monitoring perimeters.</p>
</article>
<footer>State water survey</footer>
</body></html>""",
        """Quarterly Regional Hydrological Survey Results
The regional water conservation board recorded reservoir storage volumes and watershed runoff coefficients across three river basins.
Alpine snowpack water equivalents measured twenty percent above historical averages, ensuring consistent summer meltwater replenishment for municipal distribution.
Monitoring stations recorded stable groundwater aquifer levels with negligible saline intrusion along vulnerable coastal monitoring perimeters.""",
    ),
    (
        "18_multi_column",
        """<!DOCTYPE html><html><head><title>Urban Transit Electrification</title></head><body>
<header>Metro Review</header>
<div class="col-left"><p>Quick facts</p></div>
<div class="col-center">
<article>
<h1>Comprehensive Urban Transit Electrification Study</h1>
<p>Electrifying metropolitan bus fleets yields measurable reductions in urban particulate concentrations while lowering lifecycle maintenance expenditures by thirty percent.</p>
<p>Deploying automated overhead pantograph charging at terminal turnarounds enables continuous daily route operations without requiring oversized onboard battery banks.</p>
<p>Comprehensive route telemetry confirms that regenerative braking recovers up to thirty-five percent of total kinetic energy across high-density urban transit corridors.</p>
</article>
</div>
<div class="col-right"><p>Related articles</p></div>
<footer>Transit review</footer>
</body></html>""",
        """Comprehensive Urban Transit Electrification Study
Electrifying metropolitan bus fleets yields measurable reductions in urban particulate concentrations while lowering lifecycle maintenance expenditures by thirty percent.
Deploying automated overhead pantograph charging at terminal turnarounds enables continuous daily route operations without requiring oversized onboard battery banks.
Comprehensive route telemetry confirms that regenerative braking recovers up to thirty-five percent of total kinetic energy across high-density urban transit corridors.""",
    ),
    (
        "19_announcement",
        """<!DOCTYPE html><html><head><title>Open Standards Announcement</title></head><body>
<header>Foundation News</header>
<article>
<h1>Adoption of Next-Generation Geospatial Metadata Standard</h1>
<p>The International Geospatial Consortium has formally ratified version 3.0 of the open vector coordinate reference standard.</p>
<p>The updated specification standardizes binary encoding for time-variant topological surfaces and introduces native support for centimeter-accurate satellite positioning coordinates.</p>
<p>Global open-source mapping software suites will deploy conforming parsers across their upcoming maintenance release cycles to ensure interoperability.</p>
</article>
<footer>Consortium release</footer>
</body></html>""",
        """Adoption of Next-Generation Geospatial Metadata Standard
The International Geospatial Consortium has formally ratified version 3.0 of the open vector coordinate reference standard.
The updated specification standardizes binary encoding for time-variant topological surfaces and introduces native support for centimeter-accurate satellite positioning coordinates.
Global open-source mapping software suites will deploy conforming parsers across their upcoming maintenance release cycles to ensure interoperability.""",
    ),
    (
        "20_heavy_ads_and_cookies",
        """<!DOCTYPE html><html><head><title>The Science of Deep Ocean Trenches</title></head><body>
<div id="cookie-notice"><p>Accept all tracking cookies to continue reading this website.</p></div>
<header><nav>Nav 1 | Nav 2 | Nav 3</nav></header>
<div class="advertisement-banner">Ad 1: Buy insurance today</div>
<article>
<h1>The Extreme Biology of Deep Ocean Trenches</h1>
<p>Hadal ocean trenches represent some of the most physiologically challenging biospheres on Earth, characterized by hydrostatic pressures exceeding one thousand atmospheres.</p>
<p>Endemic organisms utilize specialized piezolyte molecules and unsaturated membrane lipids to preserve cellular function and enzymatic stability in perpetual darkness.</p>
<p>Chemosynthetic microbial ecosystems thriving near serpentinizing hydrothermal vents provide critical energy inputs supporting dense communities of amphipods and snailfish.</p>
</article>
<div class="advertisement-footer">Ad 2: Book your holiday</div>
<footer>Copyright 2026 Ocean Wonders</footer>
</body></html>""",
        """The Extreme Biology of Deep Ocean Trenches
Hadal ocean trenches represent some of the most physiologically challenging biospheres on Earth, characterized by hydrostatic pressures exceeding one thousand atmospheres.
Endemic organisms utilize specialized piezolyte molecules and unsaturated membrane lipids to preserve cellular function and enzymatic stability in perpetual darkness.
Chemosynthetic microbial ecosystems thriving near serpentinizing hydrothermal vents provide critical energy inputs supporting dense communities of amphipods and snailfish.""",
    ),
]

for name, html_content, gold_content in PAGES:
    html_path = FIXTURES_DIR / f"{name}.html"
    gold_path = FIXTURES_DIR / f"{name}.gold.txt"
    html_path.write_text(html_content, encoding="utf-8")
    gold_path.write_text(gold_content, encoding="utf-8")

print(f"Generated {len(PAGES)} diverse HTML and gold fixtures in {FIXTURES_DIR}")
