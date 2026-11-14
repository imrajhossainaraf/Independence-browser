"""30-document mini corpus and 15 graded queries for Spike S2 bake-off."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MiniDoc:
    id: int
    title: str
    headings: str
    description: str
    body: str
    url_tokens: str
    lang: str = "en"


@dataclass(frozen=True)
class GradedQuery:
    query: str
    grades: dict[int, int]  # doc_id -> grade (0=irrelevant, 1=marginal, 2=relevant, 3=perfect)
    notes: str = ""


# 30 Diverse Mini Documents
DOCUMENTS: list[MiniDoc] = [
    # 1-5: SQLite & Databases
    MiniDoc(
        id=1,
        title="SQLite FTS5 Extension Guide",
        headings="Full Text Search Overview; Creating FTS5 Tables; Tokenizers",
        description="Official guide to full-text search in SQLite using the FTS5 extension.",
        body="FTS5 is an SQLite virtual table module that provides full-text search capabilities to database applications. It allows users to efficiently search documents for words or phrases. Built-in tokenizers include unicode61, porter, and trigram. Weighted BM25 ranking is supported out of the box.",
        url_tokens="sqlite org docs fts5 html",
    ),
    MiniDoc(
        id=2,
        title="Relational Database Indexing Fundamentals",
        headings="B-Trees; Hash Indexes; Full Text Indexing; Query Planning",
        description="Comprehensive guide to relational database indexing methods and trade-offs.",
        body="Database engines use indexes to accelerate data retrieval. While B-Trees excel at exact lookups and range scans, full-text indexes like inverted indexes are essential for searching unstructured text efficiently. SQLite and PostgreSQL provide specialized full-text indexing modules.",
        url_tokens="database guide relational indexing fundamentals",
    ),
    MiniDoc(
        id=3,
        title="Building Inverted Indexes in Python",
        headings="Tokenization; Inverted Index Structure; Posting Lists; BM25",
        description="Tutorial on creating inverted search indexes from scratch using Python.",
        body="An inverted index maps terms to posting lists containing document IDs and term frequencies. The BM25 algorithm scores matching documents using term frequency and inverse document frequency, balancing document length normalization.",
        url_tokens="python search building inverted indexes tutorial",
    ),
    MiniDoc(
        id=4,
        title="High Performance SQLite in Production",
        headings="WAL Mode; Busy Timeout; Concurrency; In-Memory Caching",
        description="Best practices for running SQLite concurrently with high throughput.",
        body="Configuring SQLite with Write-Ahead Logging (WAL mode) allows simultaneous readers while a single writer commits transactions. Setting a reasonable busy timeout prevents lock contention in multi-threaded Python applications.",
        url_tokens="sqlite production concurrency wal performance",
    ),
    MiniDoc(
        id=5,
        title="PostgreSQL Full Text Search vs Lucene",
        headings="Postgres tsvector; Lucene inverted index; Benchmarks",
        description="Comparison between PostgreSQL built-in search and Apache Lucene engines.",
        body="Comparing PostgreSQL full text search with Elasticsearch and Lucene. Postgres tsvector uses dictionary-based stemmers while Lucene provides richer tokenizers and distributed search clusters.",
        url_tokens="postgres full text search vs lucene comparison",
    ),
    # 6-10: Python & Programming
    MiniDoc(
        id=6,
        title="Modern Python Asynchronous Programming with Asyncio",
        headings="Event Loops; Coroutines; Tasks; Async Context Managers",
        description="Mastering async and await syntax and concurrent tasks in modern Python.",
        body="Python asyncio enables writing concurrent code using the async and await syntax. It provides an event loop for managing non-blocking I/O operations, tasks, coroutines, and network transports efficiently.",
        url_tokens="python asyncio modern asynchronous programming guide",
    ),
    MiniDoc(
        id=7,
        title="Python Multiprocessing and Process Pools",
        headings="Process Isolation; IPC; Shared Memory; Subprocesses",
        description="Deep dive into Python multiprocessing for CPU-bound workflows.",
        body="When CPU-intensive tasks bypass the Global Interpreter Lock (GIL), the multiprocessing module spawns separate OS worker processes. Process pools allow parallel batch computation with isolated memory spaces.",
        url_tokens="python multiprocessing worker process pools",
    ),
    MiniDoc(
        id=8,
        title="Concurrent Web Scraping and Crawling with Python",
        headings="Politeness; Rate Limiting; Async HTTP; HTML Parsing",
        description="Techniques for writing polite, scalable web crawlers with asyncio and httpx.",
        body="Polite crawlers adhere strictly to robots.txt, implement exponential backoff, and enforce per-host rate limiting. Using asynchronous HTTP clients such as httpx with concurrency semaphores ensures high efficiency.",
        url_tokens="python web scraping crawling async httpx politeness",
    ),
    MiniDoc(
        id=9,
        title="Linux Kernel Memory Management Explained",
        headings="Virtual Memory; Page Tables; Slab Allocator; Out of Memory Killer",
        description="Architectural analysis of memory management subsystems in the Linux kernel.",
        body="The Linux kernel manages physical and virtual memory through paging, page tables, and memory zones. The slab and slub allocators handle kernel object allocation, while the OOM killer terminates processes during severe memory exhaustion.",
        url_tokens="linux kernel systems memory management internals",
    ),
    MiniDoc(
        id=10,
        title="Systems Programming in Rust and Memory Safety",
        headings="Borrow Checker; Ownership; Concurrency; Zero-Cost Abstractions",
        description="Why systems programmers are adopting Rust for memory-safe software development.",
        body="Rust enforces memory safety without garbage collection through its static ownership and borrow checker model. It eliminates data races in concurrent programming while maintaining C-level performance.",
        url_tokens="rust systems programming memory safety concurrency",
    ),
    # 11-15: AI, Machine Learning, & Data Science
    MiniDoc(
        id=11,
        title="Deep Learning Foundations: Neural Networks from Scratch",
        headings="Backpropagation; Activation Functions; Gradient Descent; Optimization",
        description="A foundational course on artificial neural networks and deep learning algorithms.",
        body="Artificial neural networks learn complex representations through multi-layer architectures. Backpropagation calculates loss gradients with respect to weights, which stochastic gradient descent optimizes across training iterations.",
        url_tokens="deep learning neural networks foundations tutorial",
    ),
    MiniDoc(
        id=12,
        title="Transformer Architectures and Attention Mechanisms",
        headings="Self-Attention; Multi-Head Attention; Positional Encoding; BERT; GPT",
        description="How self-attention transformed natural language processing and modern machine learning.",
        body="The Transformer architecture revolutionized machine learning and deep learning by replacing recurrent networks with multi-head self-attention mechanisms, enabling massive parallel pretraining across internet text datasets.",
        url_tokens="machine learning transformers attention neural networks",
    ),
    MiniDoc(
        id=13,
        title="Artificial Intelligence Ethics, Governance, and Regulation",
        headings="Algorithmic Bias; Model Transparency; Global AI Regulations; EU AI Act",
        description="Analyzing public policy, ethical frameworks, and regulation surrounding AI models.",
        body="Governments worldwide are establishing artificial intelligence regulations such as the European Union AI Act. Key ethical concerns include algorithmic bias, privacy, explainability, autonomous decision-making, and safety standards.",
        url_tokens="artificial intelligence ethics regulation governance policy",
    ),
    MiniDoc(
        id=14,
        title="Vector Search and Dense Embeddings for Information Retrieval",
        headings="Semantic Search; Approximate Nearest Neighbor; HNSW; Hybrid Ranking",
        description="Combining dense semantic vector search with traditional sparse keyword retrieval.",
        body="Vector search indexes document embeddings generated by neural language models. Hybrid retrieval blends dense semantic vector similarity with sparse BM25 scores to achieve superior ranking accuracy across keyword and conceptual queries.",
        url_tokens="vector search embeddings semantic retrieval hybrid ranking",
    ),
    MiniDoc(
        id=15,
        title="Quantum Computing and Post-Quantum Cryptographic Security",
        headings="Qubits; Shor's Algorithm; Lattice-Based Cryptography; NIST Standards",
        description="Evaluating quantum computing threats to modern encryption and cryptographic security standards.",
        body="Quantum computers leverage qubits and quantum superposition. Shor's algorithm threatens public-key cryptography like RSA and ECC, prompting NIST to standardize quantum-resistant, post-quantum cryptographic security algorithms.",
        url_tokens="quantum computing cryptographic security post quantum encryption",
    ),
    # 16-20: Environment, Energy & Space
    MiniDoc(
        id=16,
        title="Global Climate Summit Agrees on Renewable Energy Targets",
        headings="COP Conference; Solar and Wind Expansion; Carbon Emission Reductions",
        description="World leaders at the global climate summit pledge to triple renewable energy capacity.",
        body="Delegates at the international climate summit reached a historic agreement to accelerate renewable energy deployment, expanding solar and wind infrastructure to achieve carbon emission reduction targets before 2030.",
        url_tokens="news world climate summit renewable energy agreement",
    ),
    MiniDoc(
        id=17,
        title="Electric Vehicles Battery Technology and Driving Range",
        headings="Lithium-Ion Cells; Solid-State Batteries; Fast Charging; Range Anxiety",
        description="Innovations in battery chemistry boosting electric vehicle driving range and efficiency.",
        body="Automakers are investing heavily in electric vehicles with next-generation solid-state batteries. Recent improvements in energy density deliver extended driving range exceeding 500 miles on a single charge with ultra-fast charging.",
        url_tokens="automotive electric vehicles battery range technology",
    ),
    MiniDoc(
        id=18,
        title="Solar Photovoltaic Efficiency and Grid Modernization",
        headings="Perovskite Cells; Grid Storage; Clean Energy Transition",
        description="How new photovoltaic materials are driving down clean energy generation costs.",
        body="Perovskite-silicon tandem solar cells have broken photovoltaic efficiency records. Coupled with grid-scale battery storage, solar power is rapidly replacing fossil fuels in electrical grids globally.",
        url_tokens="clean energy solar photovoltaic grid storage transition",
    ),
    MiniDoc(
        id=19,
        title="James Webb Space Telescope Major Astronomy Discoveries",
        headings="Early Galaxies; Exoplanet Atmospheres; Cosmic Dawn; Infrared Spectroscopy",
        description="Summary of groundbreaking astronomical discoveries made by the James Webb Space Telescope.",
        body="The James Webb Space Telescope has revolutionized observational astronomy, imaging early cosmic dawn galaxies formed just 300 million years after the Big Bang and characterizing atmospheric compositions of distant exoplanets.",
        url_tokens="space astronomy james webb space telescope discoveries",
    ),
    MiniDoc(
        id=20,
        title="Commercial Spaceflight and Orbital Launch Economics",
        headings="Reusable Rockets; Low Earth Orbit; Satellite Mega-Constellations",
        description="How reusable rocket boosters decreased the cost per kilogram to low Earth orbit.",
        body="Commercial aerospace companies have dramatically reduced orbital launch costs through reusable rocket boosters. Thousands of communications satellites are being deployed into low Earth orbit for global broadband internet.",
        url_tokens="aerospace commercial spaceflight orbital launch reusable",
    ),
    # 21-25: DevOps, Infrastructure & Open Source
    MiniDoc(
        id=21,
        title="Docker Containerization and Microservices Architecture",
        headings="Container Images; Dockerfile Best Practices; Multi-Stage Builds; Orchestration",
        description="Architecting scalable backend applications using Docker containerization and microservices.",
        body="Containerization packages application code and dependencies into portable Docker images. Breaking monolithic software into modular microservices simplifies continuous deployment and autonomous team scaling.",
        url_tokens="devops docker containerization microservices architecture guide",
    ),
    MiniDoc(
        id=22,
        title="Kubernetes Cluster Orchestration and Service Meshes",
        headings="Pods and Deployments; Ingress Controllers; Istio Service Mesh; Observability",
        description="Production Kubernetes design patterns for container orchestration and networking.",
        body="Kubernetes coordinates distributed containerized applications across compute node clusters. Service meshes like Istio manage mutual TLS traffic encryption, circuit breaking, and distributed tracing.",
        url_tokens="kubernetes cluster orchestration container microservices devops",
    ),
    MiniDoc(
        id=23,
        title="Open Source Software Licensing and Copyright Guide",
        headings="Permissive vs Copyleft; MIT; Apache 2.0; GPLv3; Compliance",
        description="Understanding legal obligations under permissive and copyleft open source licenses.",
        body="Choosing an open source software license determines commercial reuse rights. Permissive licenses such as MIT and Apache 2.0 grant broad freedom, whereas copyleft licenses like GPLv3 require derivative works to remain open source.",
        url_tokens="legal software open source licensing copyright compliance",
    ),
    MiniDoc(
        id=24,
        title="CI/CD Pipeline Automation with GitHub Actions",
        headings="Workflow Triggers; Matrix Builds; Automated Testing; Security Scanning",
        description="Building robust continuous integration and deployment pipelines using GitHub Actions.",
        body="Automating software testing through continuous integration pipelines catches bugs early. Workflows execute linting, type verification with mypy, unit testing suites, and container build publication automatically on git push.",
        url_tokens="devops github actions cicd automated testing pipeline",
    ),
    MiniDoc(
        id=25,
        title="Web Security: Preventing XSS, CSRF, and SQL Injection",
        headings="Input Validation; Content Security Policy; Prepared Statements; HTTPS",
        description="Essential web application security defenses against common OWASP vulnerabilities.",
        body="Securing web servers requires parameterized SQL queries to eliminate SQL injection, strict Content Security Policy headers to neutralize cross-site scripting (XSS), and SameSite cookies for CSRF defense.",
        url_tokens="security web application defenses owasp injection xss",
    ),
    # 26-30: Multilingual & International Pages (Accents, French, German, Japanese, Chinese)
    MiniDoc(
        id=26,
        title="Guide de la gastronomie française: café et restaurant",
        headings="Bistrots traditionnels; Le café parisien; Carte et menu gastronomique",
        description="Découvrez les spécialités culinaires des cafés et restaurants en France.",
        body="La gastronomie française est réputée dans le monde entier. Déguster un café dans un bistrot parisien ou savourer un repas gastronomique préparé avec passion fait partie de l'art de vivre en France. Rédiger un bon résumé culinaire.",
        url_tokens="france gastronomie cafe restaurant bistrot cuisine paris",
        lang="fr",
    ),
    MiniDoc(
        id=27,
        title="Reiseführer Berlin und München mit der Deutschen Bahn",
        headings="Hauptstadt Berlin; München in Bayern; Züge der Deutschen Bahn; Fahrplan",
        description="Entdecken Sie Berlin und München bequem mit den Fernzügen der Deutschen Bahn.",
        body="Eine Städtereise nach Berlin und München lässt sich ideal mit den ICE-Zügen der Deutschen Bahn planen. Vom Brandenburger Tor bis zum Marienplatz in München genießen Reisende Kultur, Geschichte und bayerische Gastfreundschaft.",
        url_tokens="deutschland reise berlin munchen deutsche bahn reisefuehrer",
        lang="de",
    ),
    MiniDoc(
        id=28,
        title="Tokyo Travel Guide: Exploring Historic and Modern Japan",
        headings="Shinjuku and Shibuya; Historic Asakusa; Culinary Delights; Bullet Train",
        description="Complete travel guide to Tokyo, temples, ramen shops, and modern neighborhoods.",
        body="Tokyo blends ultra-modern skyscrapers with historic temples and gardens. Visitors can ride the Shinkansen bullet train, sample artisan sushi and ramen, and explore bustling neighborhoods like Shibuya and Akihabara.",
        url_tokens="travel japan tokyo guide shinjuku shibuya temples",
        lang="en",
    ),
    MiniDoc(
        id=29,
        title="东京旅游指南：探索日本现代与传统之美",
        headings="涩谷与新宿；浅草寺历史；日本美食体验；交通指南",
        description="东京自由行全攻略，包含景点介绍、交通路线与地道日本美食推荐。",
        body="东京是日本的首都，拥有现代化的摩天大楼与古老的历史神社。从浅草寺到涩谷十字路口，游客可以体验传统茶道、享受顶级寿司与拉面，并搭乘新干线畅游日本各地。",
        url_tokens="asia travel japan tokyo guide chinese",
        lang="zh",
    ),
    MiniDoc(
        id=30,
        title="北京气候峰会：全球可再生能源发展论坛",
        headings="碳中和目标；风能与太阳能投资；国际绿色合作",
        description="北京举行国际气候论坛，聚焦可再生能源转型与全球碳减排行动合作。",
        body="在北京召开的全球气候峰会上，各国代表围绕可再生能源转型与碳排放削减展开讨论。中国在太阳能光伏与风力发电领域的投资为全球实现气候目标提供了坚实支撑。",
        url_tokens="news china beijing climate summit renewable energy",
        lang="zh",
    ),
]


# 15 Graded Queries (Grades: 3=perfect/exact, 2=relevant, 1=marginal, 0=irrelevant)
GRADED_QUERIES: list[GradedQuery] = [
    GradedQuery(
        query="sqlite fts5 full text search",
        grades={1: 3, 2: 1, 3: 2, 4: 1, 5: 1},
        notes="Technical query with exact title and body matches for SQLite FTS5.",
    ),
    GradedQuery(
        query="python async programming",
        grades={6: 3, 8: 2, 7: 1, 3: 1},
        notes="Information query targeting Python asyncio and concurrency.",
    ),
    GradedQuery(
        query="global climate summit renewable energy",
        grades={16: 3, 30: 2, 18: 1, 17: 1},
        notes="Environmental news topic with English and multilingual coverage.",
    ),
    GradedQuery(
        query="machine learning neural networks deep learning",
        grades={11: 3, 12: 3, 14: 1, 13: 1},
        notes="Broad machine learning topic testing multiple core term matches.",
    ),
    GradedQuery(
        query="quantum computing cryptographic security",
        grades={15: 3, 25: 1, 10: 0},
        notes="Science & cybersecurity query testing specialized terminology.",
    ),
    GradedQuery(
        query="french cuisine cafe resume",
        grades={26: 3, 28: 0, 1: 0},
        notes="Diacritics & accent handling query (café, résumé, cuisine, française).",
    ),
    GradedQuery(
        query="tokyo travel guide",
        grades={28: 3, 29: 2, 27: 0},
        notes="Travel query testing English vs multilingual Chinese matching.",
    ),
    GradedQuery(
        query="linux kernel memory management",
        grades={9: 3, 10: 1, 7: 1},
        notes="Operating systems query with exact title match in doc 9.",
    ),
    GradedQuery(
        query="electric vehicles battery range",
        grades={17: 3, 18: 1, 16: 1},
        notes="Automotive and clean tech query with specific compound terms.",
    ),
    GradedQuery(
        query="james webb space telescope discoveries",
        grades={19: 3, 20: 1, 15: 0},
        notes="Space exploration news with distinctive proper nouns.",
    ),
    GradedQuery(
        query="open source software licensing",
        grades={23: 3, 10: 0, 24: 1},
        notes="Software governance query testing exact conceptual match.",
    ),
    GradedQuery(
        query="docker containerization microservices",
        grades={21: 3, 22: 2, 24: 1},
        notes="DevOps architecture query testing compound technical terms.",
    ),
    GradedQuery(
        query="artificial intelligence ethics regulation",
        grades={13: 3, 11: 1, 12: 1},
        notes="AI governance and regulatory policy query.",
    ),
    GradedQuery(
        query="berlin munchen deutsche bahn",
        grades={27: 3, 26: 0, 20: 0},
        notes="German travel query testing diacritic variation (München vs munchen).",
    ),
    GradedQuery(
        query="北京 气候 峰会",
        grades={30: 3, 16: 2, 29: 1},
        notes="Chinese CJK query testing unspaced CJK and trigram search effectiveness.",
    ),
]
