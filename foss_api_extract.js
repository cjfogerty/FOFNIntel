/* ============================================================================
 * FOSS API Extractor (v8 — API-based)
 * ----------------------------------------------------------------------------
 * Replaces the v7 DOM/modal-scraping approach. Calls the FOSS JSON API directly
 * using the logged-in session's bearer token. One POST per facility returns the
 * full class catalog (group + private) WITH live spot counts inline — no
 * dropdown switching, no level-setting, no modal clicking, no PV re-scan.
 *
 * HOW TO RUN (via Claude in Chrome on a logged-in FOSS tab):
 *   1. Inject this whole file with javascript_tool.
 *   2. Call:  await window.fossApiExtractAll()   // all repo facilities
 *      or:    await window.fossApiExtract([18,35]) // specific facilityIds
 *   3. Result is placed on window.fossApiResult = { csvByFacility, errors }
 *      and each facility's CSV is also in localStorage as foss_api_csv_<slug>.
 *
 * SECURITY: reads token from localStorage('ffa_access_token') at runtime.
 * The token is never logged or written to disk by this script.
 * ==========================================================================*/

(function () {
  var API_BASE = 'https://api-account.fossswimschool.com/api';
  var TOKEN_KEY = 'ffa_access_token';

  // --- seasonId: discovered live (see getCurrentSeasonId). Fallback constant: ---
  var FALLBACK_SEASON_ID = 99; // Summer 2026 (sessionYear 2026)

  // --- Standing student/level array: casts the widest net across all distinct
  //     levels so SelectClasses/v2 returns the FULL catalog per facility.
  //     This is the constant that ELIMINATES manual level-setting. ----------
  var STUDENTS = [
    {studentId:1223932,levelId:1},{studentId:1223939,levelId:2},{studentId:937269,levelId:3},
    {studentId:1223472,levelId:5},{studentId:1223924,levelId:6},{studentId:1223933,levelId:6},
    {studentId:1223475,levelId:6},{studentId:657438,levelId:11},{studentId:1223941,levelId:11},
    {studentId:1223940,levelId:12},{studentId:1223934,levelId:10},{studentId:326679,levelId:11},
    {studentId:1223485,levelId:18},{studentId:1223925,levelId:14},{studentId:1223947,levelId:17},
    {studentId:1223935,levelId:15},{studentId:1223928,levelId:34},{studentId:1223946,levelId:19},
    {studentId:1223936,levelId:33},{studentId:289154,levelId:31},{studentId:1223943,levelId:32},
    {studentId:1223942,levelId:30},
    // --- FIX 2026-06-18: cover the FULL level ladder. SelectClasses/v2 only returns a
    //     level's full catalog when that levelId is explicitly requested; without these,
    //     M1(9), L3(7), L4(8), B3(16), BB4(4), M5(13) were almost entirely missing
    //     (e.g. Richfield was undercounting ~339 enrolled / 131 group classes).
    //     Duplicate studentId is fine — the API honors each entry's levelId independently.
    {studentId:1223472,levelId:4},{studentId:1223472,levelId:7},{studentId:1223472,levelId:8},
    {studentId:1223472,levelId:9},{studentId:1223472,levelId:13},{studentId:1223472,levelId:16}
  ];

  // --- facilityId -> dashboard slug (the 16 currently in FOFNIntel) ----------
  var FACILITIES = {
    5:'blaine', 2:'chanhassen', 31:'glenview', 8:'highland_park', 10:'lakeview',
    9:'libertyville', 3:'maple_grove', 12:'niles', 35:'northglenn', 18:'ofallon',
    23:'richfield', 1:'stlouispark', 22:'sun_prairie', 29:'western_springs',
    34:'westminster', 6:'woodbury', 11:'south_barrington'
  };

  // --- levelId -> display name (CSV "Class Level"). MUST match the prefixes
  //     update_dashboard.py keys on: "Backfloat Baby","Little","Middle","Big",
  //     "10+","Adult". Private lessons are labeled separately via accessTypeCode.
  //     Format is "Name (CODE)" to match the v7 dashboards exactly
  //     (e.g. update_dashboard.py keys category off the name prefix).
  //     Source: GET /api/Levels/GetAll (24 levels).
  var LEVEL_NAMES = {
    1:'Backfloat Baby 1 (BB1)', 2:'Backfloat Baby 2 (BB2)', 3:'Backfloat Baby 3 (BB3)', 4:'Backfloat Baby 4 (BB4)',
    5:'Little 1 (L1)', 6:'Little 2 (L2)', 7:'Little 3 (L3)', 8:'Little 4 (L4)',
    9:'Middle 1 (M1)', 10:'Middle 2 (M2)', 11:'Middle 3 (M3)', 12:'Middle 4 (M4)', 13:'Middle 5 (M5)',
    14:'Big 1 (B1)', 15:'Big 2 (B2)', 16:'Big 3 (B3)', 17:'Big 4 (B4)', 18:'Big 5 (B5)', 19:'Big 6 (B6)',
    33:'10+1 (10+1)', 34:'10+2 (10+2)',
    30:'Adult 1 (A1)', 31:'Adult 2 (A2)', 32:'Adult 3 (A3)'
  };

  function token() {
    var t = localStorage.getItem(TOKEN_KEY);
    if (!t) throw new Error('No ' + TOKEN_KEY + ' in localStorage — not logged in?');
    return t;
  }

  function api(path, method, body) {
    return fetch(API_BASE + path, {
      method: method,
      headers: {
        'authorization': 'Bearer ' + token(),
        'content-type': 'application/json',
        'accept': 'application/json, text/plain, */*'
      },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
      return r.json();
    });
  }

  function to12h(hms) {
    // "16:00:00" -> "4:00 PM"
    var p = String(hms).split(':');
    var h = parseInt(p[0], 10), m = parseInt(p[1], 10);
    var ap = h >= 12 ? 'PM' : 'AM';
    var h12 = h % 12; if (h12 === 0) h12 = 12;
    return h12 + ':' + (m < 10 ? '0' : '') + m + ' ' + ap;
  }

  // Preview lessons (intro/trial classes) ride in the weekly catalog under
  // sessionType "Preview". Label them as their own levels ("Preview <level>")
  // so dashboards can track preview slots vs enrolled separately.
  function isPreview(cls) {
    var s = ((cls.sessionTypeCode || '') + ' ' + (cls.sessionTypeCategory || '') + ' ' +
             (cls.sessionName || '')).toLowerCase();
    return s.indexOf('preview') >= 0;
  }

  function levelLabel(cls) {
    var preview = isPreview(cls);
    if (cls.accessTypeCode === 'P') return preview ? 'Preview Private (PRE)' : 'Private Lesson (PV)';
    var nm = LEVEL_NAMES[cls.levelId] || ('Level ' + cls.levelId); // visible flag if unmapped
    return preview ? 'Preview ' + nm : nm;
  }

  // A class is a 4-Week Camp if sessionTypeId === 2 (sessionTypeCode "4 Week Camp",
  // sessionTypeCategory typically "Camp"/"FourWeekCamp"). Be tolerant of both.
  function isCamp(cls) {
    if (cls.sessionTypeId === 2) return true;
    var code = (cls.sessionTypeCode || '').toLowerCase();
    var cat  = (cls.sessionTypeCategory || '').toLowerCase();
    return code.indexOf('camp') >= 0 || cat.indexOf('camp') >= 0;
  }

  function fmtDate(iso) {
    // "2026-06-15T05:00:00" -> "Jun 15"
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) { var p = String(iso).split('T')[0].split('-'); return p[1] + '/' + p[2]; }
    var mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return mo[d.getMonth()] + ' ' + d.getDate();
  }

  function campDays(cls) {
    // campWeekDays is an array like ["Monday","Wednesday"]; fall back to classDay.
    if (Array.isArray(cls.campWeekDays) && cls.campWeekDays.length) {
      return cls.campWeekDays.map(function (d) { return String(d).slice(0, 3); }).join('/');
    }
    return cls.classDay || '';
  }

  function classToRow(cls) {
    var total = (cls.totalSlots != null) ? cls.totalSlots : '';
    var open  = (cls.availableSlots != null) ? cls.availableSlots : '';
    var enrolled = (total !== '' && open !== '') ? (total - open) : '';
    var ratio = (total !== '') ? (total + ':1') : '';
    var tr = to12h(cls.classStartTime) + ' \u2013 ' + to12h(cls.classEndTime);
    return {
      'Day': cls.classDay || '',
      'Time': to12h(cls.classStartTime),
      'Class Level': levelLabel(cls),
      'Time Range': tr,
      'Spots Left': open,
      'Total Capacity': total,
      'Enrolled': enrolled,
      'Student:Teacher Ratio': ratio
    };
  }

  // Camp rows use a wider schema consumed by load_camp_csv() in update_dashboard.py:
  // Camp Name, Date Range, Days, Time, Class Level, Time Range, Spots Left,
  // Total Capacity, Enrolled, Student:Teacher Ratio
  function campToRow(cls) {
    var total = (cls.totalSlots != null) ? cls.totalSlots : '';
    var open  = (cls.availableSlots != null) ? cls.availableSlots : '';
    var enrolled = (total !== '' && open !== '') ? (total - open) : '';
    var ratio = (total !== '') ? (total + ':1') : '';
    var tr = to12h(cls.classStartTime) + ' \u2013 ' + to12h(cls.classEndTime);
    var dateRange = (cls.startDate || cls.endDate)
      ? (fmtDate(cls.startDate) + ' \u2013 ' + fmtDate(cls.endDate)) : '';
    return {
      'Camp Name': cls.sessionName || '4 Week Camp',
      'Date Range': dateRange,
      'Days': campDays(cls),
      'Time': to12h(cls.classStartTime),
      'Class Level': levelLabel(cls),
      'Time Range': tr,
      'Spots Left': open,
      'Total Capacity': total,
      'Enrolled': enrolled,
      'Student:Teacher Ratio': ratio
    };
  }

  function rowsToCsv(rows, hd) {
    var NL = String.fromCharCode(10);
    var lines = [hd.join(',')];
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], vals = [];
      for (var h = 0; h < hd.length; h++) {
        var v = (r[hd[h]] != null) ? String(r[hd[h]]) : '';
        var e = v.replace(/"/g, '""');
        vals.push(e.indexOf(',') >= 0 ? ('"' + e + '"') : e);
      }
      lines.push(vals.join(','));
    }
    return lines.join(NL);
  }

  var WEEKLY_HEADER = ['Day','Time','Class Level','Time Range','Spots Left','Total Capacity','Enrolled','Student:Teacher Ratio'];
  var CAMP_HEADER = ['Camp Name','Date Range','Days','Time','Class Level','Time Range','Spots Left','Total Capacity','Enrolled','Student:Teacher Ratio'];

  function getCurrentSeasonId(facilityId) {
    // Auto-discover the current seasonId so the script survives quarter rollover.
    // SelectClasses/v2 echoes back the seasonId it resolved; we do a probe call
    // with the fallback, read resp.seasonId, and use whatever the server returns.
    var body = { facilityId: facilityId, seasonId: FALLBACK_SEASON_ID, students: STUDENTS.slice(0,1) };
    return api('/Classes/SelectClasses/v2', 'POST', body).then(function (resp) {
      var sid = (resp && resp.seasonId) ? resp.seasonId : FALLBACK_SEASON_ID;
      console.log('[FOSS-API] Using seasonId ' + sid + ' (' +
                  (resp.sessionQuarter || '?') + ' ' + (resp.sessionYear || '?') + ')');
      return sid;
    }).catch(function () { return FALLBACK_SEASON_ID; });
  }

  function extractFacility(facilityId, seasonId) {
    var body = { facilityId: facilityId, seasonId: seasonId, students: STUDENTS };
    return api('/Classes/SelectClasses/v2', 'POST', body).then(function (resp) {
      // Dedup classes by classId across all students[] entries, then split
      // weekly (Once a Week + Preview) from 4-Week Camps.
      var seen = {}, weeklyRows = [], campRows = [], previewCount = 0;
      var students = resp.students || [];
      for (var s = 0; s < students.length; s++) {
        var classes = students[s].classes || [];
        for (var c = 0; c < classes.length; c++) {
          var cl = classes[c];
          if (seen[cl.classId]) continue;
          seen[cl.classId] = true;
          if (isPreview(cl)) previewCount++;
          if (isCamp(cl)) { campRows.push(campToRow(cl)); }
          else { weeklyRows.push(classToRow(cl)); }
        }
      }
      return {
        facilityId: facilityId,
        facilityName: resp.facilityName,
        sessionQuarter: resp.sessionQuarter,
        sessionYear: resp.sessionYear,
        availableSessionTypes: resp.availableSessionTypes || [],
        weeklyCount: weeklyRows.length,
        campCount: campRows.length,
        previewCount: previewCount,
        csv: rowsToCsv(weeklyRows, WEEKLY_HEADER),
        campCsv: campRows.length ? rowsToCsv(campRows, CAMP_HEADER) : null
      };
    });
  }

  window.fossApiExtract = function (facilityIds) {
    var ids = facilityIds || Object.keys(FACILITIES).map(Number);
    var out = { csvByFacility: {}, campCsvByFacility: {}, meta: {}, errors: [] };
    return getCurrentSeasonId(ids[0]).then(function (seasonId) {
      var chain = Promise.resolve();
      ids.forEach(function (fid) {
        chain = chain.then(function () {
          return extractFacility(fid, seasonId).then(function (res) {
            var slug = FACILITIES[fid] || ('facility_' + fid);
            out.csvByFacility[slug] = res.csv;
            try { localStorage.setItem('foss_api_csv_' + slug, res.csv); } catch (e) {}
            if (res.campCsv) {
              out.campCsvByFacility[slug] = res.campCsv;
              try { localStorage.setItem('foss_api_campcsv_' + slug, res.campCsv); } catch (e) {}
            } else {
              // Clear any stale camp CSV from a prior run so dropped camps don't linger.
              try { localStorage.removeItem('foss_api_campcsv_' + slug); } catch (e) {}
            }
            out.meta[slug] = { facilityId: fid, name: res.facilityName,
                               quarter: res.sessionQuarter, year: res.sessionYear,
                               weekly: res.weeklyCount, camps: res.campCount,
                               previews: res.previewCount };
            console.log('[FOSS-API] ' + slug + ': ' + res.weeklyCount + ' weekly + ' +
                        res.campCount + ' camp classes, ' + res.previewCount +
                        ' previews (' + res.sessionQuarter + ' ' + res.sessionYear + ')');
          }).catch(function (err) {
            out.errors.push({ facilityId: fid, error: String(err) });
            console.log('[FOSS-API] ERROR facility ' + fid + ': ' + err);
          });
        });
      });
      return chain.then(function () {
        window.fossApiResult = out;
        var campLocs = Object.keys(out.campCsvByFacility);
        console.log('[FOSS-API] DONE. ' + Object.keys(out.csvByFacility).length +
                    ' facilities, camps at ' + campLocs.length + ' (' +
                    (campLocs.join(', ') || 'none') + '), ' + out.errors.length + ' errors.');
        return out;
      });
    });
  };

  window.fossApiExtractAll = function () { return window.fossApiExtract(null); };

  console.log('[FOSS-API] v8 loaded. Run: await window.fossApiExtractAll()');
})();
