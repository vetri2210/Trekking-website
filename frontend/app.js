const { createApp } = Vue;

createApp({
  data() {
    return {
      user: null,
      page: "discover",
      treks: [],
      bookings: [],
      users: [],
      staff: [],
      adminData: { stats: {}, recent_bookings: [] },
      staffTreks: [],
      exports: [],
      filters: { q: "", difficulty: "", location: "", min_days: "", max_days: "" },
      trekSearch: "",
      accountSearch: "",
      accountRole: "",
      alert: null,
      alertTimer: null,
      loading: true,
      mobileMenu: false,
      modal: null,
      form: {},
      participants: [],
      selectedTrek: null,
      exportTimer: null,
    };
  },
  computed: {
    greeting() {
      const hour = new Date().getHours();
      return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    },
    upcomingBookings() {
      return this.bookings.filter((booking) => booking.status === "Booked");
    },
  },
  async mounted() {
    try {
      const response = await this.request("/api/session");
      this.user = response.user;
      if (this.user?.role === "admin") this.page = "admin";
      else if (this.user?.role === "staff") this.page = "staff";
      await this.refreshPage();
    } catch (error) {
      this.notify(error.message, true);
    } finally {
      this.loading = false;
    }
  },
  beforeUnmount() {
    clearInterval(this.exportTimer);
    clearTimeout(this.alertTimer);
  },
  methods: {
    async request(url, options = {}) {
      const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers: {
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {}),
        },
      });
      const data = response.status === 204 ? {} : await response.json();
      if (!response.ok) throw new Error(data.error || "Something went wrong. Please try again.");
      return data;
    },
    async refreshPage() {
      if (this.page === "admin") {
        if (this.user?.role !== "admin") return;
        const [dashboard, treks, staff, users, bookings] = await Promise.all([
          this.request("/api/admin/dashboard"),
          this.request(`/api/treks?q=${encodeURIComponent(this.trekSearch)}`),
          this.request("/api/admin/staff"),
          this.request(`/api/admin/users?q=${encodeURIComponent(this.accountSearch)}&role=${encodeURIComponent(this.accountRole)}`),
          this.request("/api/admin/bookings"),
        ]);
        this.adminData = dashboard;
        this.treks = treks.treks;
        this.staff = staff.staff;
        this.users = users.users;
        this.bookings = bookings.bookings;
      } else if (this.page === "staff") {
        if (this.user?.role !== "staff") return;
        const result = await this.request("/api/staff/dashboard");
        this.staffTreks = result.treks;
      } else if (this.page === "bookings" || this.page === "profile") {
        if (!this.user) return;
        if (this.user.role === "user") {
          const [bookings, exports] = await Promise.all([
            this.request("/api/bookings"),
            this.request("/api/exports"),
          ]);
          this.bookings = bookings.bookings;
          this.exports = exports.exports;
        }
        const profile = await this.request("/api/profile");
        this.user = profile.user;
      } else {
        const params = new URLSearchParams();
        Object.entries(this.filters).forEach(([key, value]) => {
          if (value) params.set(key, value);
        });
        const result = await this.request(`/api/treks?${params}`);
        this.treks = result.treks;
      }
    },
    async go(page) {
      this.page = page;
      this.mobileMenu = false;
      try {
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    async searchTreks() {
      this.page = "discover";
      await this.go("discover");
    },
    notify(message, error = false) {
      this.alert = { message, error };
      clearTimeout(this.alertTimer);
      this.alertTimer = setTimeout(() => (this.alert = null), 3800);
    },
    formatDate(value) {
      if (!value) return "Date to be confirmed";
      const parsed = new Date(`${value}T00:00:00`);
      return parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    },
    statusClass(status) {
      return String(status || "").toLowerCase().replaceAll(" ", "-");
    },
    openAuth(mode) {
      this.form = { mode, name: "", phone: "", email: "", password: "" };
      this.modal = { type: "auth", title: mode === "login" ? "Welcome back" : "Your next chapter starts here" };
    },
    async submitAuth() {
      try {
        const endpoint = this.form.mode === "login" ? "/api/auth/login" : "/api/auth/register";
        const result = await this.request(endpoint, { method: "POST", body: JSON.stringify(this.form) });
        this.user = result.user;
        this.modal = null;
        this.page = this.user.role === "admin" ? "admin" : this.user.role === "staff" ? "staff" : "discover";
        await this.refreshPage();
        this.notify(this.form.mode === "login" ? `Lovely to have you back, ${this.user.name.split(" ")[0]}.` : "Welcome to the trail community!");
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async logout() {
      try {
        await this.request("/api/auth/logout", { method: "POST" });
        this.user = null;
        this.page = "discover";
        this.bookings = [];
        await this.refreshPage();
        this.notify("You have been signed out. See you soon!");
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async book(trek) {
      if (!this.user) {
        this.openAuth("register");
        return;
      }
      if (this.user.role !== "user") {
        this.notify("Trek bookings are available to trekker accounts.", true);
        return;
      }
      try {
        const result = await this.request("/api/bookings", {
          method: "POST",
          body: JSON.stringify({ trek_id: trek.id }),
        });
        this.notify(result.message);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async cancelBooking(booking) {
      if (!confirm(`Cancel your booking for ${booking.name || booking.trek_name}?`)) return;
      try {
        const result = await this.request(`/api/bookings/${booking.id}/cancel`, { method: "PATCH" });
        this.notify(result.message);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    openTrekForm(trek = null) {
      this.form = trek
        ? { ...trek }
        : {
            name: "",
            location: "",
            difficulty: "Easy",
            duration_days: 1,
            available_slots: 10,
            status: "Pending",
            staff_id: "",
            start_date: "",
            end_date: "",
            description: "",
            image_url: "",
          };
      this.modal = { type: "trek", title: trek ? "A little trek tune-up" : "Add a new adventure", trekId: trek?.id };
    },
    async saveTrek() {
      try {
        const editing = Boolean(this.modal.trekId);
        await this.request(editing ? `/api/admin/treks/${this.modal.trekId}` : "/api/admin/treks", {
          method: editing ? "PUT" : "POST",
          body: JSON.stringify(this.form),
        });
        this.modal = null;
        this.notify(editing ? "Trek details saved." : "Your new trek is on the map.");
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async removeTrek(trek) {
      if (!confirm(`Remove ${trek.name}? Treks with booking history can be closed instead.`)) return;
      try {
        const result = await this.request(`/api/admin/treks/${trek.id}`, { method: "DELETE" });
        this.notify(result.message);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    openStaffForm() {
      this.form = { name: "", email: "", phone: "", password: "" };
      this.modal = { type: "staff", title: "Welcome a new trek lead" };
    },
    async saveStaff() {
      try {
        const result = await this.request("/api/admin/staff", { method: "POST", body: JSON.stringify(this.form) });
        this.modal = null;
        this.notify(`${this.form.name} can now sign in as trek staff.`);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async changeAccountStatus(account, status) {
      const action = status === "active" ? "reactivate" : status === "blacklisted" ? "blacklist" : "deactivate";
      if (!confirm(`${action[0].toUpperCase()}${action.slice(1)} ${account.name}'s account?`)) return;
      try {
        const result = await this.request(`/api/admin/users/${account.id}/status`, {
          method: "PATCH",
          body: JSON.stringify({ status }),
        });
        this.notify(result.message);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async manageTrek(trek) {
      this.form = { available_slots: trek.available_slots, status: trek.status };
      this.modal = { type: "staffTrek", title: trek.name, trekId: trek.id };
    },
    async saveStaffTrek() {
      try {
        const result = await this.request(`/api/staff/treks/${this.modal.trekId}`, {
          method: "PATCH",
          body: JSON.stringify(this.form),
        });
        this.modal = null;
        this.notify(result.message);
        await this.refreshPage();
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async showParticipants(trek) {
      try {
        const result = await this.request(`/api/staff/treks/${trek.id}/participants`);
        this.participants = result.participants;
        this.selectedTrek = trek;
        this.modal = { type: "participants", title: `Your people · ${trek.name}` };
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async searchAccounts() {
      try {
        const result = await this.request(`/api/admin/users?q=${encodeURIComponent(this.accountSearch)}&role=${encodeURIComponent(this.accountRole)}`);
        this.users = result.users;
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async searchAdminTreks() {
      try {
        const result = await this.request(`/api/treks?q=${encodeURIComponent(this.trekSearch)}`);
        this.treks = result.treks;
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async saveProfile() {
      try {
        const result = await this.request("/api/profile", {
          method: "PATCH",
          body: JSON.stringify({ name: this.form.name, phone: this.form.phone }),
        });
        this.user = result.user;
        this.notify("Your profile is up to date.");
      } catch (error) {
        this.notify(error.message, true);
      }
    },
    async requestExport() {
      try {
        const result = await this.request("/api/exports", { method: "POST" });
        this.notify(result.message);
        await this.refreshPage();
        clearInterval(this.exportTimer);
        this.exportTimer = setInterval(async () => {
          try {
            await this.refreshPage();
            if (!this.exports.some((item) => item.status === "queued" || item.status === "started")) {
              clearInterval(this.exportTimer);
              this.notify("Your CSV is ready to download.");
            }
          } catch (error) {
            clearInterval(this.exportTimer);
            this.notify(error.message, true);
          }
        }, 3000);
      } catch (error) {
        this.notify(error.message, true);
      }
    },
  },
  template: `
  <div class="app-shell">
    <header class="topbar">
      <div class="nav-inner">
        <a class="brand" href="#" @click.prevent="go(user?.role === 'admin' ? 'admin' : user?.role === 'staff' ? 'staff' : 'discover')">
          <span class="brand-mark">t</span><span>trailwise</span>
        </a>
        <button class="menu-toggle" aria-label="Toggle navigation" @click="mobileMenu = !mobileMenu">☰</button>
        <nav class="nav-links" :class="{ 'mobile-open': mobileMenu }">
          <button :class="{active: page === 'discover'}" @click="go('discover')">Find a trek</button>
          <template v-if="user?.role === 'user'">
            <button :class="{active: page === 'bookings'}" @click="go('bookings')">My journeys</button>
            <button :class="{active: page === 'profile'}" @click="go('profile')">My profile</button>
          </template>
          <template v-if="user?.role === 'admin'">
            <button :class="{active: page === 'admin'}" @click="go('admin')">Overview</button>
          </template>
          <template v-if="user?.role === 'staff'">
            <button :class="{active: page === 'staff'}" @click="go('staff')">My assigned treks</button>
          </template>
        </nav>
        <div class="nav-actions">
          <template v-if="user">
            <span class="user-chip"><span class="avatar">{{ user.name?.slice(0,1) }}</span><span>{{ user.name?.split(' ')[0] }}</span></span>
            <button class="btn-outline-trail" @click="logout">Sign out</button>
          </template>
          <template v-else>
            <button class="btn-outline-trail" @click="openAuth('login')">Log in</button>
            <button class="btn-trail" @click="openAuth('register')">Join the trail</button>
          </template>
        </div>
      </div>
    </header>

    <template v-if="!loading && page === 'discover'">
      <section class="hero">
        <div class="hero-content">
          <span class="eyebrow">Good days are out there</span>
          <h1>Find your<br>kind of <em>wild.</em></h1>
          <p>Fresh air, new friends, and a trail that stays with you long after the last step. Your next little escape starts here.</p>
          <form class="hero-search" @submit.prevent="searchTreks">
            <input v-model="filters.q" aria-label="Search treks" placeholder="Where do you feel like wandering?" />
            <button class="btn-trail" type="submit">Find my trail <span aria-hidden="true">→</span></button>
          </form>
          <div class="trust-row"><span>✦ Small, thoughtful groups</span><span>✦ Local trail leaders</span></div>
        </div>
        <span class="hero-caption">The Western Ghats, India · a little morning magic</span>
      </section>

      <main class="main-content" id="treks">
        <div class="section-head">
          <div><div class="section-kicker">A good place to begin</div><h2>Trips worth getting up for</h2><p>Hand-picked escapes, led by people who know the way.</p></div>
          <button v-if="user?.role === 'user'" class="btn-outline-trail" @click="go('bookings')">Your journeys →</button>
        </div>
        <div class="filters">
          <input v-model="filters.q" class="filter-input search" placeholder="⌕  Search a trail or place" @input="searchTreks" />
          <select v-model="filters.difficulty" class="filter-select" @change="searchTreks"><option value="">All paces</option><option>Easy</option><option>Moderate</option><option>Hard</option></select>
          <input v-model="filters.location" class="filter-input" placeholder="Any location" @input="searchTreks" />
          <select v-model="filters.min_days" class="filter-select" @change="searchTreks"><option value="">Any duration</option><option value="1">1+ days</option><option value="2">2+ days</option><option value="3">3+ days</option></select>
          <button v-if="filters.q || filters.difficulty || filters.location || filters.min_days" class="small-button" @click="filters = {q:'',difficulty:'',location:'',min_days:'',max_days:''}; searchTreks()">Clear filters</button>
        </div>
        <div v-if="treks.length" class="trek-grid">
          <article class="trek-card" v-for="trek in treks" :key="trek.id">
            <div class="trek-image"><img :src="trek.image_url || 'https://images.unsplash.com/photo-1470770841072-f978cf4d019e?auto=format&fit=crop&w=1200&q=85'" :alt="trek.name" /><span class="difficulty-chip">{{ trek.difficulty }} pace</span><span class="date-chip">{{ formatDate(trek.start_date) }}</span></div>
            <div class="trek-body">
              <div class="trek-location">⌖ &nbsp;{{ trek.location }}</div>
              <h3>{{ trek.name }}</h3>
              <p class="trek-description">{{ trek.description || 'A little time outdoors, a lovely route, and a story to take home.' }}</p>
              <div class="trek-meta"><span>◷ &nbsp;{{ trek.duration_days }} {{ trek.duration_days === 1 ? 'day' : 'days' }}</span><span>⌁ &nbsp;{{ trek.staff_name || 'Local trail leader' }}</span></div>
              <div class="trek-footer"><span class="slots"><strong>{{ trek.available_slots }}</strong> places left</span><button class="btn-soft" :disabled="trek.available_slots < 1" @click="book(trek)">{{ user?.role === 'user' ? 'Save my spot' : 'Join this trek' }} →</button></div>
            </div>
          </article>
        </div>
        <div v-else class="empty-state"><div class="empty-icon">🌿</div><h3>No trails found just yet</h3><p>Try a different place or clear the filters. Your kind of wild is out there.</p></div>
      </main>
    </template>

    <main v-if="!loading && page === 'admin' && user?.role === 'admin'" class="dashboard-layout">
      <div class="dashboard-top"><div><div class="section-kicker">Your little command centre</div><h1 class="page-title">{{ greeting }}, {{ user.name?.split(' ')[0] }}.</h1><p>Here's what's happening across the Trailwise community.</p></div><button class="btn-trail" @click="openTrekForm()">＋ &nbsp;Create a trek</button></div>
      <div class="stats-grid">
        <div class="stat-card"><span class="stat-label">All treks</span><strong class="stat-value">{{ adminData.stats.treks || 0 }}</strong></div>
        <div class="stat-card"><span class="stat-label">Active trekkers</span><strong class="stat-value">{{ adminData.stats.users || 0 }}</strong></div>
        <div class="stat-card"><span class="stat-label">Trail staff</span><strong class="stat-value">{{ adminData.stats.staff || 0 }}</strong></div>
        <div class="stat-card"><span class="stat-label">Active bookings</span><strong class="stat-value">{{ adminData.stats.bookings || 0 }}</strong></div>
      </div>
      <section class="panel">
        <div class="panel-title"><h3>Routes & upcoming adventures</h3><div class="inline-actions"><input class="filter-input" v-model="trekSearch" @input="searchAdminTreks" placeholder="Search trek or location" /><button class="btn-soft" @click="openTrekForm()">＋ Add trek</button></div></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Trek</th><th>When</th><th>Places left</th><th>Trail lead</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody><tr v-for="trek in treks" :key="trek.id"><td><strong>{{ trek.name }}</strong><div class="text-secondary small">{{ trek.location }} · {{ trek.difficulty }}</div></td><td>{{ formatDate(trek.start_date) }}</td><td>{{ trek.available_slots }}</td><td>{{ trek.staff_name || 'Unassigned' }}</td><td><span class="status-pill" :class="statusClass(trek.status)">{{ trek.status }}</span></td><td><div class="inline-actions"><button class="small-button" @click="openTrekForm(trek)">Edit</button><button class="small-button danger-button" @click="removeTrek(trek)">Remove</button></div></td></tr>
          <tr v-if="!treks.length"><td colspan="6" class="text-center text-secondary py-4">No treks yet. Add the first adventure.</td></tr></tbody></table></div>
      </section>
      <section class="panel">
        <div class="panel-title"><h3>People on the trail</h3><button class="btn-soft" @click="openStaffForm()">＋ Add staff</button></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Assigned treks</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody><tr v-for="member in staff" :key="member.id"><td><strong>{{ member.name }}</strong></td><td>{{ member.email }}</td><td>{{ member.phone || '—' }}</td><td>{{ member.assigned_treks }}</td><td><span class="status-pill" :class="statusClass(member.status)">{{ member.status }}</span></td><td><button class="small-button" @click="changeAccountStatus(member, member.status === 'active' ? 'deactivated' : 'active')">{{ member.status === 'active' ? 'Deactivate' : 'Reactivate' }}</button></td></tr>
          <tr v-if="!staff.length"><td colspan="6" class="text-center text-secondary py-4">Your team will show up here once you add a trail lead.</td></tr></tbody></table></div>
      </section>
      <section class="panel">
        <div class="panel-title"><h3>Community accounts</h3><div class="inline-actions"><input class="filter-input" v-model="accountSearch" @input="searchAccounts" placeholder="Search name or email" /><select class="filter-select" v-model="accountRole" @change="searchAccounts"><option value="">Everyone</option><option value="user">Trekkers</option><option value="staff">Trail staff</option></select></div></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead>
          <tbody><tr v-for="account in users" :key="account.id"><td>{{ account.name }}</td><td>{{ account.email }}</td><td>{{ account.role === 'staff' ? 'Trail staff' : 'Trekker' }}</td><td><span class="status-pill" :class="statusClass(account.status)">{{ account.status }}</span></td><td>{{ formatDate(account.created_at?.slice(0,10)) }}</td><td><button class="small-button" @click="changeAccountStatus(account, account.status === 'active' ? 'blacklisted' : 'active')">{{ account.status === 'active' ? 'Blacklist' : 'Reactivate' }}</button></td></tr></tbody></table></div>
      </section>
      <section class="panel"><div class="panel-title"><h3>Booking history</h3><span class="section-kicker">{{ bookings.length }} records</span></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Guest</th><th>Adventure</th><th>Location</th><th>Booked on</th><th>Status</th></tr></thead><tbody>
          <tr v-for="booking in bookings" :key="booking.id"><td><strong>{{ booking.user_name }}</strong><div class="text-secondary small">{{ booking.email }}</div></td><td>{{ booking.trek_name }}</td><td>{{ booking.location }}</td><td>{{ formatDate(booking.booking_date?.slice(0,10)) }}</td><td><span class="status-pill" :class="statusClass(booking.status)">{{ booking.status }}</span></td></tr>
          <tr v-if="!bookings.length"><td colspan="5" class="text-center text-secondary py-4">No bookings yet. The next adventure is just around the corner.</td></tr></tbody></table></div>
      </section>
    </main>

    <main v-if="!loading && page === 'staff' && user?.role === 'staff'" class="dashboard-layout">
      <div class="dashboard-top"><div><div class="section-kicker">Your trail notes</div><h1 class="page-title">{{ greeting }}, {{ user.name?.split(' ')[0] }}.</h1><p>Every good adventure starts with a thoughtful guide.</p></div></div>
      <section class="panel"><div class="panel-title"><h3>Your assigned treks</h3><span class="section-kicker">{{ staffTreks.length }} adventures</span></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Trek</th><th>Date</th><th>Registered</th><th>Places left</th><th>Status</th><th>Manage</th></tr></thead><tbody>
          <tr v-for="trek in staffTreks" :key="trek.id"><td><strong>{{ trek.name }}</strong><div class="text-secondary small">{{ trek.location }}</div></td><td>{{ formatDate(trek.start_date) }}</td><td>{{ trek.registered }}</td><td>{{ trek.available_slots }}</td><td><span class="status-pill" :class="statusClass(trek.status)">{{ trek.status }}</span></td><td><div class="inline-actions"><button class="small-button" @click="manageTrek(trek)">Update</button><button class="small-button" @click="showParticipants(trek)">Participants</button></div></td></tr>
          <tr v-if="!staffTreks.length"><td colspan="6" class="text-center text-secondary py-4">No treks have been assigned to you yet. Your admin will add them here.</td></tr></tbody></table></div>
      </section>
    </main>

    <main v-if="!loading && page === 'bookings' && user?.role === 'user'" class="dashboard-layout">
      <div class="dashboard-top"><div><div class="section-kicker">A collection of good days</div><h1 class="page-title">Your journeys, {{ user.name?.split(' ')[0] }}.</h1><p>Your next step, and all the stories already in your pack.</p></div><button class="btn-outline-trail" @click="requestExport">↓ &nbsp;Export history</button></div>
      <div v-if="upcomingBookings.length" class="trek-grid">
        <article class="trek-card" v-for="booking in bookings" :key="booking.id">
          <div class="trek-image"><img :src="booking.image_url || 'https://images.unsplash.com/photo-1470770841072-f978cf4d019e?auto=format&fit=crop&w=1200&q=85'" :alt="booking.name" /><span class="difficulty-chip">{{ booking.difficulty }}</span><span class="date-chip">{{ booking.status }}</span></div>
          <div class="trek-body"><div class="trek-location">⌖ &nbsp;{{ booking.location }}</div><h3>{{ booking.name }}</h3><p class="trek-description">Your adventure begins {{ formatDate(booking.start_date) }}. Take a breath, get outside, and enjoy every step.</p>
            <div class="trek-meta"><span>◷ &nbsp;{{ formatDate(booking.start_date) }}</span><span class="status-pill" :class="statusClass(booking.status)">{{ booking.status }}</span></div>
            <div class="trek-footer"><span class="slots">Booked {{ formatDate(booking.booking_date?.slice(0,10)) }}</span><button v-if="booking.status === 'Booked'" class="small-button danger-button" @click="cancelBooking(booking)">Cancel booking</button></div>
          </div>
        </article>
      </div>
      <div v-else class="empty-state"><div class="empty-icon">🥾</div><h3>Your next story is waiting</h3><p>You haven't booked a trek yet. Find a pace and place that feels like you.</p><button class="btn-trail mt-2" @click="go('discover')">Find a trek →</button></div>
      <section class="panel mt-4"><div class="panel-title"><h3>Your CSV exports</h3><span class="section-kicker">Prepared just for you</span></div>
        <div v-if="exports.length" class="table-wrap"><table class="table"><thead><tr><th>Requested</th><th>Status</th><th>Action</th></tr></thead><tbody><tr v-for="item in exports" :key="item.id"><td>{{ formatDate(item.created_at?.slice(0,10)) }}</td><td><span class="status-pill" :class="statusClass(item.status)">{{ item.status }}</span><span v-if="item.error" class="text-danger small d-block">{{ item.error }}</span></td><td><a v-if="item.download_url" class="small-button text-decoration-none" :href="item.download_url">Download CSV ↓</a><span v-else class="text-secondary small">We'll let you know when it's ready.</span></td></tr></tbody></table></div>
        <p v-else class="text-secondary small mb-0">Your export requests will appear here. We'll put together your full booking history as a CSV.</p>
      </section>
    </main>

    <main v-if="!loading && page === 'profile' && user?.role === 'user'" class="dashboard-layout">
      <div class="dashboard-top"><div><div class="section-kicker">The person behind the boots</div><h1 class="page-title">Your profile</h1><p>Keep your details up to date so your trail leader can reach you.</p></div></div>
      <section class="panel profile-grid"><form @submit.prevent="saveProfile"><div class="mb-3"><label class="form-label">Your name</label><input v-model="user.name" class="form-control" required minlength="2" /></div><div class="mb-3"><label class="form-label">Email address</label><input :value="user.email" class="form-control" disabled /><div class="form-text">Email is tied to your account and cannot be changed here.</div></div><div class="mb-4"><label class="form-label">Phone number</label><input v-model="user.phone" class="form-control" type="tel" placeholder="A number your guide can reach" /></div><button class="btn-trail" type="submit">Save my details</button></form></section>
    </main>

    <div v-if="loading" class="page-loading"><span class="loading-mark">t</span><span>Finding your trail…</span></div>
    <footer v-if="!loading" class="footer"><span>© 2026 Trailwise · Made for the long way round.</span><span>Go gently. Leave it better than you found it.</span></footer>

    <div v-if="modal" class="modal d-block" tabindex="-1" role="dialog" @click.self="modal = null">
      <div class="modal-dialog modal-dialog-centered modal-lg" :class="{'modal-md': modal.type === 'auth'}"><div class="modal-content">
        <div class="modal-header"><div><div class="section-kicker">Trailwise</div><h2 class="modal-title fs-5">{{ modal.title }}</h2></div><button class="btn-close" aria-label="Close" @click="modal = null"></button></div>
        <div class="modal-body">
          <form v-if="modal.type === 'auth'" @submit.prevent="submitAuth">
            <p class="auth-note mb-4">{{ form.mode === 'login' ? 'Pick up right where you left off.' : 'Make room for fresh air, good company, and a few new favourite places.' }}</p>
            <div v-if="form.mode === 'register'" class="mb-3"><label class="form-label">Your name</label><input v-model="form.name" class="form-control" required minlength="2" autocomplete="name" /></div>
            <div v-if="form.mode === 'register'" class="mb-3"><label class="form-label">Phone (optional)</label><input v-model="form.phone" class="form-control" type="tel" autocomplete="tel" /></div>
            <div class="mb-3"><label class="form-label">Email address</label><input v-model="form.email" class="form-control" required type="email" autocomplete="email" /></div>
            <div class="mb-3"><label class="form-label">Password</label><input v-model="form.password" class="form-control" required type="password" minlength="8" :autocomplete="form.mode === 'login' ? 'current-password' : 'new-password'" /></div>
            <p v-if="form.mode === 'login'" class="auth-note">Staff and admin accounts are created by your Trailwise administrator.</p>
            <div class="modal-footer px-0 pb-0"><button type="button" class="btn-outline-trail" @click="openAuth(form.mode === 'login' ? 'register' : 'login')">{{ form.mode === 'login' ? 'Create account' : 'Already a member?' }}</button><button class="btn-trail" type="submit">{{ form.mode === 'login' ? 'Come on in' : 'Create my account' }} →</button></div>
          </form>
          <form v-else-if="modal.type === 'trek'" @submit.prevent="saveTrek">
            <div class="row g-3">
              <div class="col-md-7"><label class="form-label">Trek name</label><input v-model="form.name" class="form-control" required /></div>
              <div class="col-md-5"><label class="form-label">Location</label><input v-model="form.location" class="form-control" required /></div>
              <div class="col-md-4"><label class="form-label">Difficulty</label><select v-model="form.difficulty" class="form-select"><option>Easy</option><option>Moderate</option><option>Hard</option></select></div>
              <div class="col-md-4"><label class="form-label">Duration (days)</label><input v-model.number="form.duration_days" class="form-control" type="number" min="1" required /></div>
              <div class="col-md-4"><label class="form-label">Available places</label><input v-model.number="form.available_slots" class="form-control" type="number" min="0" required /></div>
              <div class="col-md-4"><label class="form-label">Start date</label><input v-model="form.start_date" class="form-control" type="date" required /></div>
              <div class="col-md-4"><label class="form-label">End date</label><input v-model="form.end_date" class="form-control" type="date" required /></div>
              <div class="col-md-4"><label class="form-label">Trek status</label><select v-model="form.status" class="form-select"><option>Pending</option><option>Approved</option><option>Open</option><option>Closed</option><option>Completed</option></select></div>
              <div class="col-md-6"><label class="form-label">Assign trail staff</label><select v-model="form.staff_id" class="form-select"><option value="">Not assigned yet</option><option v-for="member in staff.filter(item => item.status === 'active')" :key="member.id" :value="member.id">{{ member.name }} · {{ member.assigned_treks }} trek(s)</option></select></div>
              <div class="col-md-6"><label class="form-label">Cover image URL (optional)</label><input v-model="form.image_url" class="form-control" type="url" /></div>
              <div class="col-12"><label class="form-label">A little about this trek</label><textarea v-model="form.description" class="form-control" rows="3" placeholder="What should trekkers look forward to?"></textarea></div>
            </div>
            <p class="auth-note mt-3 mb-0">Dates and duration should line up. New routes are saved as Pending by default.</p>
            <div class="modal-footer px-0 pb-0"><button type="button" class="btn-outline-trail" @click="modal = null">Never mind</button><button class="btn-trail" type="submit">Save adventure</button></div>
          </form>
          <form v-else-if="modal.type === 'staff'" @submit.prevent="saveStaff">
            <p class="auth-note mb-4">A staff account lets your trail lead sign in, see their assigned treks, and look after their participants.</p>
            <div class="mb-3"><label class="form-label">Full name</label><input v-model="form.name" class="form-control" required minlength="2" /></div>
            <div class="mb-3"><label class="form-label">Email address</label><input v-model="form.email" class="form-control" type="email" required /></div>
            <div class="mb-3"><label class="form-label">Phone (optional)</label><input v-model="form.phone" class="form-control" type="tel" /></div>
            <div class="mb-3"><label class="form-label">Temporary password</label><input v-model="form.password" class="form-control" type="password" minlength="8" required /><div class="form-text">Share it privately and ask them to change it after signing in.</div></div>
            <div class="modal-footer px-0 pb-0"><button type="button" class="btn-outline-trail" @click="modal = null">Cancel</button><button class="btn-trail" type="submit">Create staff account</button></div>
          </form>
          <form v-else-if="modal.type === 'staffTrek'" @submit.prevent="saveStaffTrek">
            <p class="auth-note mb-4">Keep the practical details up to date for your group.</p>
            <div class="mb-3"><label class="form-label">Available places</label><input v-model.number="form.available_slots" class="form-control" type="number" min="0" required /></div>
            <div class="mb-3"><label class="form-label">Trek status</label><select v-model="form.status" class="form-select"><option>Open</option><option>Closed</option><option>Completed</option></select><div class="form-text">Marking a trek completed will update its active bookings to Completed.</div></div>
            <div class="modal-footer px-0 pb-0"><button type="button" class="btn-outline-trail" @click="modal = null">Cancel</button><button class="btn-trail" type="submit">Save trek details</button></div>
          </form>
          <div v-else-if="modal.type === 'participants'">
            <div v-if="participants.length" class="table-wrap"><table class="table"><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Booking</th></tr></thead><tbody><tr v-for="person in participants" :key="person.id"><td>{{ person.name }}</td><td>{{ person.email }}</td><td>{{ person.phone || '—' }}</td><td><span class="status-pill">{{ person.status }}</span></td></tr></tbody></table></div>
            <div v-else class="empty-state"><div class="empty-icon">🌱</div><h3>The group is still gathering</h3><p>There are no registered participants for this trek yet.</p></div>
            <div class="modal-footer px-0 pb-0"><button class="btn-trail" @click="modal = null">All good</button></div>
          </div>
        </div>
      </div></div>
    </div>
    <div v-if="modal" class="modal-backdrop show" @click="modal = null"></div>
    <div v-if="alert" class="toast-alert" :class="{error: alert.error}" role="status">{{ alert.message }}</div>
  </div>
  `,
}).mount("#app");
