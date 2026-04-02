import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
from datetime import datetime
import os
import logging
from typing import List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DATABASE_FILE = 'beast_orders.db'
ORDERS_PER_FREE = 20
ALLOWED_ROLES = [
    1465627226639827039,
    1465627423583240193,
    1486176813599424512
]

class BeastOrderBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        self.db = None
        
    async def setup_hook(self):
        """Initialize database and sync commands"""
        await self.init_database()
        await self.tree.sync()
        logger.info("Bot setup complete and commands synced")
        
    async def init_database(self):
        """Initialize SQLite database"""
        self.db = await aiosqlite.connect(DATABASE_FILE)
        self.db.row_factory = aiosqlite.Row
        
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("PRAGMA cache_size=10000")
        
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                total_orders INTEGER DEFAULT 0,
                current_cycle INTEGER DEFAULT 0,
                free_orders_earned INTEGER DEFAULT 0,
                last_log_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await self.db.execute('''
            CREATE TABLE IF NOT EXISTS order_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                logged_by TEXT NOT NULL,
                logged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                order_number INTEGER NOT NULL,
                is_free_order BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        await self.db.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON order_logs(user_id)')
        await self.db.execute('CREATE INDEX IF NOT EXISTS idx_logged_at ON order_logs(logged_at)')
        
        await self.db.commit()
        logger.info("Database initialized successfully")
        
    async def get_user_stats(self, user_id: str):
        """Get user statistics from database"""
        cursor = await self.db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return await cursor.fetchone()
        
    async def create_or_update_user(self, user_id: str, username: str):
        """Create new user or update existing user"""
        now = datetime.now().isoformat()
        existing = await self.get_user_stats(user_id)
        
        if existing:
            await self.db.execute('UPDATE users SET username = ?, updated_at = ? WHERE user_id = ?', 
                                (username, now, user_id))
        else:
            await self.db.execute('INSERT INTO users (user_id, username, last_log_date) VALUES (?, ?, ?)', 
                                (user_id, username, now))
        await self.db.commit()
        
    async def log_order(self, user_id: str, username: str, logged_by: str):
        """Log a new order for a user"""
        now = datetime.now().isoformat()
        user_stats = await self.get_user_stats(user_id)
        
        if not user_stats:
            await self.create_or_update_user(user_id, username)
            user_stats = await self.get_user_stats(user_id)
        
        current_cycle = user_stats['current_cycle']
        total_orders = user_stats['total_orders']
        free_orders_earned = user_stats['free_orders_earned']
        
        is_free_order = False
        if current_cycle >= ORDERS_PER_FREE:
            is_free_order = True
            current_cycle = 0
            free_orders_earned += 1
            logger.info(f"User {username} earned a free order!")
        
        new_order_number = total_orders + 1
        
        await self.db.execute('''
            INSERT INTO order_logs (user_id, username, logged_by, logged_at, order_number, is_free_order)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, logged_by, now, new_order_number, is_free_order))
        
        await self.db.execute('''
            UPDATE users SET total_orders = ?, current_cycle = ?, free_orders_earned = ?, 
                           last_log_date = ?, updated_at = ? WHERE user_id = ?
        ''', (new_order_number, current_cycle + 1, free_orders_earned, now, now, user_id))
        
        await self.db.commit()
        
        return {
            'order_number': new_order_number,
            'current_cycle': current_cycle + 1,
            'total_orders': new_order_number,
            'free_orders_earned': free_orders_earned,
            'is_free_order': is_free_order,
            'orders_until_free': ORDERS_PER_FREE - (current_cycle + 1)
        }
        
    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.close()
        await super().close()

# Initialize bot
bot = BeastOrderBot()

def check_allowed_roles(interaction: discord.Interaction) -> bool:
    """Check if user has AT LEAST ONE of the allowed roles"""
    if isinstance(interaction.user, discord.Member):
        user_roles = [role.id for role in interaction.user.roles]
        # Returns True if user has ANY of the allowed roles (only needs one)
        return any(role_id in user_roles for role_id in ALLOWED_ROLES)
    return False

async def username_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    """Autocomplete function to suggest server member usernames"""
    # Get all members from the guild
    if interaction.guild is None:
        return []
    
    # Search through members and find matches
    choices = []
    for member in interaction.guild.members:
        # Check if current text is in username, display name, or nickname
        name_to_check = member.display_name.lower()
        if current.lower() in name_to_check or current.lower() in member.name.lower():
            # Format: @username (limit to 100 characters for Discord API)
            choice_name = f"@{member.display_name}"
            if len(choice_name) > 100:
                choice_name = choice_name[:97] + "..."
            
            choices.append(app_commands.Choice(name=choice_name, value=member.name))
            
            # Limit to 25 choices max (Discord limit)
            if len(choices) >= 25:
                break
    
    return choices

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} guild(s)')

@bot.tree.command(name="log", description="Log a Beast order for a user")
@app_commands.describe(username="The username to log the order for")
@app_commands.autocomplete(username=username_autocomplete)
@app_commands.check(check_allowed_roles)
async def log_command(interaction: discord.Interaction, username: str):
    """Log a Beast order for the specified username"""
    await interaction.response.defer()
    
    try:
        username = username.strip().lstrip('@')
        user_id = str(hash(username))
        
        stats = await bot.log_order(user_id=user_id, username=username, logged_by=interaction.user.name)
        
        embed = discord.Embed(
            title="✅ Beast Order Completed",
            color=discord.Color.green() if not stats['is_free_order'] else discord.Color.gold()
        )
        
        embed.add_field(name="👤 User", value=f"@{username}", inline=False)
        embed.add_field(name="📊 Beast Orders Logged", value=f"{stats['current_cycle']}/{ORDERS_PER_FREE}", inline=False)
        
        progress = stats['current_cycle'] / ORDERS_PER_FREE
        filled_blocks = int(progress * 20)
        empty_blocks = 20 - filled_blocks
        progress_bar = f"🟩{'' * filled_blocks}{'⬛' * empty_blocks}"
        embed.add_field(name="Progress", value=progress_bar, inline=False)
        
        if stats['is_free_order']:
            embed.add_field(name="🎉 FREE ORDER!", value="This order is FREE!", inline=False)
            embed.color = discord.Color.gold()
        else:
            embed.add_field(name="🎁 Beast free order in:", value=f"{stats['orders_until_free']} more", inline=False)
        
        embed.add_field(name="📈 Total Orders", value=str(stats['total_orders']), inline=True)
        embed.add_field(name="🎟️ Free Orders Earned", value=str(stats['free_orders_earned']), inline=True)
        embed.set_footer(text=f"Logged by: {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
        embed.timestamp = datetime.now()
        
        await interaction.followup.send(embed=embed)
        logger.info(f"Order logged for {username} by {interaction.user.name}")
        
    except Exception as e:
        logger.error(f"Error logging order: {e}", exc_info=True)
        await interaction.followup.send("❌ An error occurred while logging the order. Please try again.", ephemeral=True)

@log_command.error
async def log_command_error(interaction: discord.Interaction, error):
    """Handle errors for the log command"""
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You don't have permission to use this command. You need at least one of the required roles.", ephemeral=True)
    else:
        logger.error(f"Command error: {error}", exc_info=True)
        await interaction.response.send_message("❌ An error occurred. Please try again.", ephemeral=True)

@bot.tree.command(name="stats", description="View order statistics for a user")
@app_commands.describe(username="The username to view stats for")
@app_commands.autocomplete(username=username_autocomplete)
async def stats_command(interaction: discord.Interaction, username: str):
    """View statistics for a specific user"""
    await interaction.response.defer()
    
    try:
        username = username.strip().lstrip('@')
        user_id = str(hash(username))
        user_stats = await bot.get_user_stats(user_id)
        
        if not user_stats:
            await interaction.followup.send(f"❌ No orders found for @{username}", ephemeral=True)
            return
        
        embed = discord.Embed(title=f"📊 Statistics for @{username}", color=discord.Color.blue())
        embed.add_field(name="Total Orders", value=str(user_stats['total_orders']), inline=True)
        embed.add_field(name="Current Cycle", value=f"{user_stats['current_cycle']}/{ORDERS_PER_FREE}", inline=True)
        embed.add_field(name="Free Orders Earned", value=str(user_stats['free_orders_earned']), inline=True)
        
        if user_stats['last_log_date']:
            embed.add_field(name="Last Order", value=user_stats['last_log_date'][:10], inline=True)
        
        embed.set_footer(text=f"User ID: {user_id[:8]}...")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error fetching stats: {e}", exc_info=True)
        await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)

@bot.tree.command(name="reset", description="Reset a user's order count (Admin only)")
@app_commands.describe(username="The username to reset")
@app_commands.autocomplete(username=username_autocomplete)
@app_commands.check(check_allowed_roles)
async def reset_command(interaction: discord.Interaction, username: str):
    """Reset a user's order statistics"""
    await interaction.response.defer()
    
    try:
        username = username.strip().lstrip('@')
        user_id = str(hash(username))
        
        await bot.db.execute('''
            UPDATE users SET total_orders = 0, current_cycle = 0, free_orders_earned = 0, updated_at = ?
            WHERE user_id = ?
        ''', (datetime.now().isoformat(), user_id))
        await bot.db.commit()
        
        await interaction.followup.send(f"✅ Reset order statistics for @{username}", ephemeral=True)
        logger.info(f"Reset stats for {username} by {interaction.user.name}")
        
    except Exception as e:
        logger.error(f"Error resetting stats: {e}", exc_info=True)
        await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)

async def main():
    """Main entry point"""
    token = os.getenv('DISCORD_TOKEN')
    
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set!")
        return
        
    try:
        await bot.start(token)
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())