"""Generate reviewed seed YAML with fixed UUIDv5 values.

Run only when intentionally editing the catalog below. Runtime seeding consumes
the generated YAML and never derives identifiers.
"""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "grocea" / "seed_data"

CATEGORIES = [
    ("produce", "Produce"),
    ("dairy", "Dairy"),
    ("meat-seafood", "Meat & Seafood"),
    ("grains-bakery", "Grains & Bakery"),
    ("pantry", "Pantry"),
    ("frozen", "Frozen"),
    ("beverages", "Beverages"),
    ("other", "Other"),
]

INGREDIENTS = [
    # Produce (40)
    ("banana", "Banana", "produce", "count"),
    ("apple", "Apple", "produce", "count"),
    ("orange", "Orange", "produce", "count"),
    ("lime", "Lime", "produce", "count"),
    ("lemon", "Lemon", "produce", "count"),
    ("mango", "Mango", "produce", "count"),
    ("papaya", "Papaya", "produce", "count"),
    ("pineapple", "Pineapple", "produce", "count"),
    ("watermelon", "Watermelon", "produce", "count"),
    ("dragon-fruit", "Dragon fruit", "produce", "count"),
    ("avocado", "Avocado", "produce", "count"),
    ("coconut", "Coconut", "produce", "count"),
    ("tomato", "Tomato", "produce", "count"),
    ("cherry-tomatoes", "Cherry tomatoes", "produce", "mass"),
    ("cucumber", "Cucumber", "produce", "count"),
    ("carrot", "Carrot", "produce", "count"),
    ("potato", "Potato", "produce", "mass"),
    ("sweet-potato", "Sweet potato", "produce", "mass"),
    ("onion", "Onion", "produce", "count"),
    ("red-onion", "Red onion", "produce", "count"),
    ("garlic", "Garlic", "produce", "mass"),
    ("ginger", "Ginger", "produce", "mass"),
    ("galangal", "Galangal", "produce", "mass"),
    ("lemongrass", "Lemongrass", "produce", "count"),
    ("spring-onion", "Spring onion", "produce", "mass"),
    ("coriander-leaves", "Coriander leaves", "produce", "mass"),
    ("curry-leaves", "Curry leaves", "produce", "mass"),
    ("pandan-leaves", "Pandan leaves", "produce", "count"),
    ("spinach", "Spinach", "produce", "mass"),
    ("water-spinach", "Water spinach", "produce", "mass"),
    ("cabbage", "Cabbage", "produce", "count"),
    ("bok-choy", "Bok choy", "produce", "count"),
    ("broccoli", "Broccoli", "produce", "count"),
    ("cauliflower", "Cauliflower", "produce", "count"),
    ("eggplant", "Eggplant", "produce", "count"),
    ("okra", "Okra", "produce", "mass"),
    ("green-beans", "Green beans", "produce", "mass"),
    ("bean-sprouts", "Bean sprouts", "produce", "mass"),
    ("red-chilli", "Red chilli", "produce", "mass"),
    ("birds-eye-chilli", "Bird's eye chilli", "produce", "mass"),
    # Dairy (10)
    ("whole-milk", "Whole milk", "dairy", "volume"),
    ("low-fat-milk", "Low-fat milk", "dairy", "volume"),
    ("butter", "Butter", "dairy", "mass"),
    ("cheddar-cheese", "Cheddar cheese", "dairy", "mass"),
    ("mozzarella", "Mozzarella", "dairy", "mass"),
    ("cream-cheese", "Cream cheese", "dairy", "mass"),
    ("plain-yogurt", "Plain yogurt", "dairy", "mass"),
    ("greek-yogurt", "Greek yogurt", "dairy", "mass"),
    ("cooking-cream", "Cooking cream", "dairy", "volume"),
    ("condensed-milk", "Condensed milk", "dairy", "mass"),
    # Meat & Seafood (25)
    ("chicken-breast", "Chicken breast", "meat-seafood", "mass"),
    ("chicken-thigh", "Chicken thigh", "meat-seafood", "mass"),
    ("whole-chicken", "Whole chicken", "meat-seafood", "mass"),
    ("minced-chicken", "Minced chicken", "meat-seafood", "mass"),
    ("beef-steak", "Beef steak", "meat-seafood", "mass"),
    ("minced-beef", "Minced beef", "meat-seafood", "mass"),
    ("beef-brisket", "Beef brisket", "meat-seafood", "mass"),
    ("lamb-shoulder", "Lamb shoulder", "meat-seafood", "mass"),
    ("minced-lamb", "Minced lamb", "meat-seafood", "mass"),
    ("pork-belly", "Pork belly", "meat-seafood", "mass"),
    ("pork-loin", "Pork loin", "meat-seafood", "mass"),
    ("duck-breast", "Duck breast", "meat-seafood", "mass"),
    ("egg", "Egg", "meat-seafood", "count"),
    ("salmon-fillet", "Salmon fillet", "meat-seafood", "mass"),
    ("tuna", "Tuna", "meat-seafood", "mass"),
    ("mackerel", "Mackerel", "meat-seafood", "mass"),
    ("sardine", "Sardine", "meat-seafood", "mass"),
    ("prawn", "Prawn", "meat-seafood", "mass"),
    ("squid", "Squid", "meat-seafood", "mass"),
    ("mussel", "Mussel", "meat-seafood", "mass"),
    ("clam", "Clam", "meat-seafood", "mass"),
    ("crab", "Crab", "meat-seafood", "mass"),
    ("fish-ball", "Fish ball", "meat-seafood", "mass"),
    ("chicken-sausage", "Chicken sausage", "meat-seafood", "mass"),
    ("dried-anchovy", "Dried anchovy", "meat-seafood", "mass"),
    # Grains & Bakery (20)
    ("white-rice", "White rice", "grains-bakery", "mass"),
    ("basmati-rice", "Basmati rice", "grains-bakery", "mass"),
    ("jasmine-rice", "Jasmine rice", "grains-bakery", "mass"),
    ("brown-rice", "Brown rice", "grains-bakery", "mass"),
    ("glutinous-rice", "Glutinous rice", "grains-bakery", "mass"),
    ("rolled-oats", "Rolled oats", "grains-bakery", "mass"),
    ("wheat-flour", "Wheat flour", "grains-bakery", "mass"),
    ("rice-flour", "Rice flour", "grains-bakery", "mass"),
    ("corn-flour", "Corn flour", "grains-bakery", "mass"),
    ("white-bread", "White bread", "grains-bakery", "count"),
    ("wholemeal-bread", "Wholemeal bread", "grains-bakery", "count"),
    ("burger-bun", "Burger bun", "grains-bakery", "count"),
    ("yellow-noodles", "Yellow noodles", "grains-bakery", "mass"),
    ("rice-noodles", "Rice noodles", "grains-bakery", "mass"),
    ("rice-vermicelli", "Rice vermicelli", "grains-bakery", "mass"),
    ("spaghetti", "Spaghetti", "grains-bakery", "mass"),
    ("macaroni", "Macaroni", "grains-bakery", "mass"),
    ("couscous", "Couscous", "grains-bakery", "mass"),
    ("quinoa", "Quinoa", "grains-bakery", "mass"),
    ("breakfast-cereal", "Breakfast cereal", "grains-bakery", "mass"),
    # Pantry (35)
    ("cooking-oil", "Cooking oil", "pantry", "volume"),
    ("olive-oil", "Olive oil", "pantry", "volume"),
    ("sesame-oil", "Sesame oil", "pantry", "volume"),
    ("coconut-milk", "Coconut milk", "pantry", "volume"),
    ("soy-sauce", "Soy sauce", "pantry", "volume"),
    ("dark-soy-sauce", "Dark soy sauce", "pantry", "volume"),
    ("fish-sauce", "Fish sauce", "pantry", "volume"),
    ("oyster-sauce", "Oyster sauce", "pantry", "volume"),
    ("white-vinegar", "White vinegar", "pantry", "volume"),
    ("rice-vinegar", "Rice vinegar", "pantry", "volume"),
    ("salt", "Salt", "pantry", "mass"),
    ("white-sugar", "White sugar", "pantry", "mass"),
    ("brown-sugar", "Brown sugar", "pantry", "mass"),
    ("black-pepper", "Black pepper", "pantry", "mass"),
    ("white-pepper", "White pepper", "pantry", "mass"),
    ("curry-powder", "Curry powder", "pantry", "mass"),
    ("ground-turmeric", "Ground turmeric", "pantry", "mass"),
    ("ground-cumin", "Ground cumin", "pantry", "mass"),
    ("ground-coriander", "Ground coriander", "pantry", "mass"),
    ("paprika", "Paprika", "pantry", "mass"),
    ("chilli-powder", "Chilli powder", "pantry", "mass"),
    ("ground-cinnamon", "Ground cinnamon", "pantry", "mass"),
    ("star-anise", "Star anise", "pantry", "mass"),
    ("clove", "Clove", "pantry", "mass"),
    ("cardamom", "Cardamom", "pantry", "mass"),
    ("canned-chickpeas", "Canned chickpeas", "pantry", "mass"),
    ("canned-tomatoes", "Canned tomatoes", "pantry", "mass"),
    ("tomato-paste", "Tomato paste", "pantry", "mass"),
    ("peanut-butter", "Peanut butter", "pantry", "mass"),
    ("sambal", "Sambal", "pantry", "mass"),
    ("belacan", "Belacan", "pantry", "mass"),
    ("tofu", "Tofu", "pantry", "mass"),
    ("tempeh", "Tempeh", "pantry", "mass"),
    ("dried-lentils", "Dried lentils", "pantry", "mass"),
    ("dried-red-beans", "Dried red beans", "pantry", "mass"),
    # Frozen (5)
    ("frozen-peas", "Frozen peas", "frozen", "mass"),
    ("frozen-mixed-vegetables", "Frozen mixed vegetables", "frozen", "mass"),
    ("frozen-corn", "Frozen corn", "frozen", "mass"),
    ("fish-finger", "Fish finger", "frozen", "count"),
    ("ice-cream", "Ice cream", "frozen", "mass"),
    # Beverages (10)
    ("coffee-beans", "Coffee beans", "beverages", "mass"),
    ("ground-coffee", "Ground coffee", "beverages", "mass"),
    ("instant-coffee", "Instant coffee", "beverages", "mass"),
    ("black-tea-bag", "Black tea bag", "beverages", "count"),
    ("green-tea-bag", "Green tea bag", "beverages", "count"),
    ("cocoa-powder", "Cocoa powder", "beverages", "mass"),
    ("orange-juice", "Orange juice", "beverages", "volume"),
    ("apple-juice", "Apple juice", "beverages", "volume"),
    ("sparkling-water", "Sparkling water", "beverages", "volume"),
    ("coconut-water", "Coconut water", "beverages", "volume"),
    # Other (5)
    ("baking-powder", "Baking powder", "other", "mass"),
    ("baking-soda", "Baking soda", "other", "mass"),
    ("dry-yeast", "Dry yeast", "other", "mass"),
    ("vanilla-extract", "Vanilla extract", "other", "volume"),
    ("food-colouring", "Food colouring", "other", "volume"),
]


def fixed_id(kind: str, slug: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"https://grocea.local/{kind}/{slug}"))


def main() -> None:
    if len(INGREDIENTS) != 150:
        raise SystemExit(f"Expected 150 ingredients, found {len(INGREDIENTS)}")
    if len({item[0] for item in INGREDIENTS}) != len(INGREDIENTS):
        raise SystemExit("Ingredient slugs must be unique")
    OUTPUT.mkdir(parents=True, exist_ok=True)

    category_lines = ["categories:"]
    for slug, name in CATEGORIES:
        category_lines.extend(
            [
                f"  - id: {fixed_id('categories', slug)}",
                f"    key: {slug}",
                f"    name: {name}",
            ]
        )
    (OUTPUT / "categories.yaml").write_text("\n".join(category_lines) + "\n", encoding="utf-8")

    ingredient_lines = ["ingredients:"]
    for slug, name, category, family in INGREDIENTS:
        escaped_name = name.replace("'", "''")
        ingredient_lines.extend(
            [
                f"  - id: {fixed_id('ingredients', slug)}",
                f"    name: '{escaped_name}'",
                f"    category: {category}",
                f"    measurement_family: {family}",
            ]
        )
    (OUTPUT / "ingredients.yaml").write_text("\n".join(ingredient_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
