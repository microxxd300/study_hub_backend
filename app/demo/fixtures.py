"""Pre-generated study content for the shared demo account (no AI calls needed)."""

TOPICS = [
    {"name": "Calculus", "color": "iris", "weight": 0.30},
    {"name": "Biology", "color": "lime", "weight": 0.25},
    {"name": "World History", "color": "amber", "weight": 0.18},
    {"name": "Spanish", "color": "rose", "weight": 0.15},
    {"name": "Physics", "color": "sky", "weight": 0.12},
]

LABELS = {
    "Calculus": [
        "derivatives",
        "chain rule",
        "integrals",
        "limits",
        "related rates",
        "u-substitution",
    ],
    "Biology": [
        "photosynthesis",
        "cell respiration",
        "mitosis",
        "genetics",
        "enzymes",
        "ecology",
    ],
    "World History": [
        "French Revolution",
        "Cold War",
        "Industrial Revolution",
        "WWI causes",
        "Renaissance",
    ],
    "Spanish": [
        "preterite vs imperfect",
        "subjunctive",
        "vocab: travel",
        "ser vs estar",
        "listening",
    ],
    "Physics": ["kinematics", "Newton's laws", "energy", "momentum", "circuits"],
}


def _q(question: str, options: list[str], answer: int, explanation: str) -> dict:
    return {
        "question": question,
        "options": options,
        "answer_index": answer,
        "explanation": explanation,
    }


NOTES = [
    {
        "topic": "Biology",
        "title": "Photosynthesis: light reactions & Calvin cycle",
        "source_kind": "image",
        "summary": (
            "Photosynthesis converts light energy into chemical energy stored in glucose. It happens in "
            "two linked stages inside the chloroplast: the light-dependent reactions in the thylakoid "
            "membranes make ATP and NADPH and release O2 from water, and the Calvin cycle in the stroma "
            "uses that ATP and NADPH to fix CO2 into sugar. Rubisco is the enzyme that fixes carbon."
        ),
        "key_points": [
            "Overall: 6CO2 + 6H2O + light -> C6H12O6 + 6O2.",
            "Light reactions occur in the thylakoid membranes and produce ATP, NADPH and O2.",
            "The oxygen released comes from splitting water (photolysis), not from CO2.",
            "The Calvin cycle runs in the stroma and fixes CO2 using ATP and NADPH.",
            "Rubisco catalyses the first step of carbon fixation.",
            "Chlorophyll absorbs mostly red and blue light and reflects green.",
        ],
        "flashcards": [
            (
                "Where do the light-dependent reactions take place?",
                "In the thylakoid membranes of the chloroplast.",
            ),
            ("What are the products of the light reactions?", "ATP, NADPH and O2."),
            (
                "Where does the O2 released in photosynthesis come from?",
                "From splitting water (photolysis).",
            ),
            ("Where does the Calvin cycle occur?", "In the stroma of the chloroplast."),
            ("Which enzyme fixes CO2 in the Calvin cycle?", "Rubisco."),
            (
                "Why do leaves look green?",
                "Chlorophyll reflects green light and absorbs red and blue.",
            ),
        ],
        "quiz": [
            _q(
                "Which molecule is split to release oxygen during photosynthesis?",
                ["Carbon dioxide", "Glucose", "Water", "ATP"],
                2,
                "Photolysis of water releases O2.",
            ),
            _q(
                "The Calvin cycle takes place in the…",
                ["Thylakoid lumen", "Stroma", "Mitochondrial matrix", "Cytoplasm"],
                1,
                "Carbon fixation happens in the stroma.",
            ),
            _q(
                "Which products of the light reactions power the Calvin cycle?",
                ["ATP and NADPH", "Glucose and O2", "CO2 and water", "ADP and NADP+"],
                0,
                "ATP and NADPH carry energy and electrons into the Calvin cycle.",
            ),
            _q(
                "What does rubisco do?",
                ["Splits water", "Absorbs light", "Fixes CO2 to RuBP", "Makes ATP"],
                2,
                "Rubisco attaches CO2 to RuBP, the first step of carbon fixation.",
            ),
            _q(
                "Which wavelengths does chlorophyll absorb least?",
                ["Red", "Blue", "Violet", "Green"],
                3,
                "Green is reflected, which is why leaves look green.",
            ),
        ],
    },
    {
        "topic": "Calculus",
        "title": "Derivative rules cheat sheet",
        "source_kind": "pdf",
        "summary": (
            "The derivative measures instantaneous rate of change. Most derivatives can be found by "
            "combining a few rules: the power rule for polynomials, the product and quotient rules for "
            "products and ratios, and the chain rule for compositions. Derivatives of sin, cos, e^x and "
            "ln x are worth memorising."
        ),
        "key_points": [
            "Power rule: d/dx x^n = n x^(n-1).",
            "Product rule: (fg)' = f'g + fg'.",
            "Quotient rule: (f/g)' = (f'g - fg') / g^2.",
            "Chain rule: (f(g(x)))' = f'(g(x)) * g'(x).",
            "d/dx sin x = cos x, d/dx cos x = -sin x.",
            "d/dx e^x = e^x and d/dx ln x = 1/x.",
        ],
        "flashcards": [
            ("State the power rule.", "d/dx xⁿ = n·xⁿ⁻¹."),
            ("State the product rule.", "(fg)' = f'g + fg'."),
            ("State the quotient rule.", "(f/g)' = (f'g − fg') / g²."),
            ("State the chain rule.", "d/dx f(g(x)) = f'(g(x))·g'(x)."),
            ("d/dx cos x = ?", "−sin x."),
            ("d/dx ln x = ?", "1/x (for x > 0)."),
            ("d/dx e^(3x) = ?", "3e^(3x), by the chain rule."),
        ],
        "quiz": [
            _q(
                "What is d/dx of x^5?",
                ["5x^4", "x^4", "5x^5", "4x^5"],
                0,
                "Power rule: bring down 5, subtract 1.",
            ),
            _q(
                "What is d/dx of sin(2x)?",
                ["cos(2x)", "2cos(2x)", "-2cos(2x)", "2sin(2x)"],
                1,
                "Chain rule: cos(2x) times the inner derivative 2.",
            ),
            _q(
                "Which rule do you need for x²·eˣ?",
                ["Chain rule", "Quotient rule", "Product rule", "Power rule only"],
                2,
                "It's a product of two functions.",
            ),
            _q(
                "What is d/dx of ln(x)?",
                ["x", "1/x", "ln(x)/x", "e^x"],
                1,
                "A standard result for x > 0.",
            ),
            _q(
                "What is d/dx of cos(x)?",
                ["sin(x)", "-sin(x)", "-cos(x)", "tan(x)"],
                1,
                "The derivative of cosine is negative sine.",
            ),
        ],
    },
    {
        "topic": "World History",
        "title": "Causes of the French Revolution",
        "source_kind": "image",
        "summary": (
            "The French Revolution (1789) grew from a fiscal crisis layered on deep social inequality. "
            "War debts, including support for the American Revolution, bankrupted the crown, while the "
            "Third Estate carried most of the tax burden. Enlightenment ideas questioned absolute "
            "monarchy, and poor harvests drove bread prices up. Calling the Estates-General to fix "
            "finances turned into a constitutional confrontation."
        ),
        "key_points": [
            "France was nearly bankrupt after costly wars, including aid to the American Revolution.",
            "The Ancien Régime split society into three estates; the Third Estate paid most taxes.",
            "Enlightenment thinkers like Rousseau and Montesquieu challenged absolute monarchy.",
            "Poor harvests in 1788 sent bread prices soaring.",
            "The Estates-General met in May 1789; the Third Estate formed the National Assembly.",
            "The storming of the Bastille on 14 July 1789 became the Revolution's symbol.",
        ],
        "flashcards": [
            ("What year did the French Revolution begin?", "1789."),
            (
                "Which estate bore most of the tax burden?",
                "The Third Estate (commoners).",
            ),
            (
                "What body did the Third Estate form in June 1789?",
                "The National Assembly.",
            ),
            ("What happened on 14 July 1789?", "The storming of the Bastille."),
            (
                "Name two Enlightenment thinkers who influenced the Revolution.",
                "Rousseau and Montesquieu (also Voltaire).",
            ),
            (
                "Why was the Estates-General called?",
                "To address the crown's financial crisis.",
            ),
        ],
        "quiz": [
            _q(
                "Which group made up the Third Estate?",
                ["Clergy", "Nobility", "Commoners", "Royal family"],
                2,
                "The Third Estate was everyone outside the clergy and nobility.",
            ),
            _q(
                "What immediate problem led Louis XVI to call the Estates-General?",
                [
                    "A foreign invasion",
                    "A financial crisis",
                    "A plague",
                    "A succession dispute",
                ],
                1,
                "The crown needed new taxes to cover its debts.",
            ),
            _q(
                "The Tennis Court Oath was taken by…",
                ["The nobility", "The National Assembly", "The Jacobins", "The king"],
                1,
                "Deputies vowed not to disband until France had a constitution.",
            ),
            _q(
                "Which event became the symbol of the Revolution?",
                [
                    "The Reign of Terror",
                    "The storming of the Bastille",
                    "The Flight to Varennes",
                    "Napoleon's coup",
                ],
                1,
                "14 July is still France's national day.",
            ),
            _q(
                "Which writer argued for the 'general will'?",
                ["Voltaire", "Locke", "Rousseau", "Hobbes"],
                2,
                "Rousseau's The Social Contract.",
            ),
        ],
    },
    {
        "topic": "Spanish",
        "title": "Preterite vs imperfect",
        "source_kind": "text",
        "summary": (
            "Spanish has two simple past tenses. The preterite describes completed actions with a clear "
            "beginning or end; the imperfect describes ongoing, habitual or background actions, and "
            "descriptions such as time, age and feelings in the past. A common pattern is the imperfect "
            "setting the scene and the preterite interrupting it."
        ),
        "key_points": [
            "Preterite: completed, specific events (Ayer comí pizza).",
            "Imperfect: habits, ongoing actions and descriptions (De niño, comía pizza cada viernes).",
            "Time and age in the past use the imperfect (Eran las tres; tenía diez años).",
            "Trigger words: ayer, anoche, una vez -> preterite; siempre, a menudo, mientras -> imperfect.",
            "Interrupted action: imperfect for the background, preterite for the interruption.",
        ],
        "flashcards": [
            ("Which past tense for a completed, one-time action?", "Preterite."),
            ("Which past tense for habitual actions?", "Imperfect."),
            ("'It was three o'clock' in Spanish?", "Eran las tres (imperfect)."),
            (
                "'Mientras leía, sonó el teléfono' — which verb is the interruption?",
                "Sonó (preterite).",
            ),
            ("Trigger word 'siempre' usually signals…", "The imperfect."),
        ],
        "quiz": [
            _q(
                "Ayer ___ (ir) al cine.",
                ["iba", "fui", "voy", "iré"],
                1,
                "A specific completed event: preterite.",
            ),
            _q(
                "Cuando era niño, ___ (jugar) al fútbol cada día.",
                ["jugué", "juego", "jugaba", "jugaré"],
                2,
                "A childhood habit: imperfect.",
            ),
            _q(
                "Which tense describes age in the past?",
                ["Preterite", "Imperfect", "Future", "Present perfect"],
                1,
                "Tenía diez años.",
            ),
            _q(
                "Mientras ___ (cocinar), llegó mi hermano.",
                ["cociné", "cocinaba", "cocino", "cocinaré"],
                1,
                "Background action interrupted: imperfect.",
            ),
            _q(
                "Which trigger word usually takes the preterite?",
                ["siempre", "a menudo", "anoche", "mientras"],
                2,
                "'Last night' marks a specific completed time.",
            ),
        ],
    },
]

TOPIC_QUIZ = {
    "topic": "Physics",
    "cache_key": "topic:newton's laws of motion",
    "title": "Newton's laws of motion",
    "questions": [
        _q(
            "Newton's first law is also called the law of…",
            ["Acceleration", "Inertia", "Gravity", "Momentum"],
            1,
            "Objects keep their state of motion unless a net force acts.",
        ),
        _q(
            "F = ma is Newton's…",
            ["First law", "Second law", "Third law", "Law of gravitation"],
            1,
            "Net force equals mass times acceleration.",
        ),
        _q(
            "A 2 kg mass accelerates at 3 m/s². What is the net force?",
            ["1.5 N", "5 N", "6 N", "9 N"],
            2,
            "F = 2 × 3 = 6 N.",
        ),
        _q(
            "Action-reaction force pairs act on…",
            [
                "The same object",
                "Different objects",
                "Only moving objects",
                "Only in a vacuum",
            ],
            1,
            "Third-law pairs always act on two different bodies.",
        ),
        _q(
            "If net force is zero, an object's velocity…",
            ["Increases", "Decreases", "Stays constant", "Becomes zero"],
            2,
            "No net force means no acceleration.",
        ),
    ],
}
