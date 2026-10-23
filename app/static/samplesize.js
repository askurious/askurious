// Sample size calculator (/sample-size/), computed live in the browser.
//
// Part 1 (also loaded by tests/test_samplesize.py under Node) holds the mathematics: exact power of the t-tests
// (noncentral t), of the one-way ANOVA (noncentral F) and of the test of a correlation (exact distribution of r),
// the smallest sample that reaches the power, and plan(), which turns the fields of the page into the shown
// numbers. Part 2 is the page.

(function (root) {
  "use strict";

  // the language of the page (app/lang.py; de_en of app.js); German under Node (tests/test_samplesize.py)
  function de_en(de, en) { return root.de_en ? root.de_en(de, en) : de; }
  var LOCALE = typeof document !== "undefined" && document.documentElement.lang === "en" ? "en-GB" : "de-DE";

  // ---- special functions ---------------------------------------------------------------------------------
  // log Γ(x) by the Lanczos approximation (Lanczos, 1964) in its standard form, with the published coefficients for
  // g = 7, n = 9 (Godfrey); below 0.5 by the reflection formula
  var LANCZOS = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  function lgamma(x) {
    if (x < 0.5) return Math.log(Math.PI / Math.abs(Math.sin(Math.PI * x))) - lgamma(1 - x);
    x -= 1;
    var a = LANCZOS[0], t = x + 7.5;
    for (var i = 1; i < 9; i++) a += LANCZOS[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }

  // Value of the continued fraction 1 / (b(1) + a(2) / (b(2) + a(3) / (b(3) + …))), part(n) = [a(n), b(n)],
  // by the modified Lentz method (Lentz, 1976; Thompson & Barnett, 1986), until a step changes it by less than tol.
  var TINY = 1e-300;
  function fraction(part, steps, tol) {
    var value = TINY, num = TINY, den = 0, n, p, step;
    for (n = 1; n <= steps; n++) {
      p = n === 1 ? [1, part(1)[1]] : part(n);
      den = p[1] + p[0] * den;
      den = 1 / (Math.abs(den) < TINY ? TINY : den);
      num = p[1] + p[0] / num;
      if (Math.abs(num) < TINY) num = TINY;
      step = num * den;
      value *= step;
      if (Math.abs(step - 1) < tol) break;
    }
    return value;
  }

  // regularized incomplete gamma Q(a, x) = 1 − P(a, x): below x = a + 1 the power series of P, above it
  // Legendre's continued fraction of Q (Abramowitz & Stegun, 1964, 6.5.29 and 6.5.31)
  function upperGamma(a, x) {
    if (x <= 0) return 1;
    var scale = Math.exp(-x + a * Math.log(x) - lgamma(a));
    if (x < a + 1) {
      var add = 1 / a, total = add;
      for (var n = 1; n < 1000 && Math.abs(add) >= Math.abs(total) * 1e-16; n++) {
        add *= x / (a + n);
        total += add;
      }
      return 1 - scale * total;
    }
    return scale * fraction(function (n) { return [-(n - 1) * (n - 1 - a), x + 2 * n - 1 - a]; }, 1000, 1e-16);
  }

  // standard normal distribution function
  function pnorm(x) {
    var q = 0.5 * upperGamma(0.5, x * x / 2);
    return x < 0 ? q : 1 - q;
  }

  // inverse standard normal distribution function by P. J. Acklam's rational approximation, with the coefficients
  // published by P. J. Acklam; relative error below about 1.15e-9
  function qnorm(p) {
    var a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716, 2.506628277459239],
        b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572],
        c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783],
        d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416], q, r;
    if (p < 0.02425) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    if (p > 1 - 0.02425) { q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    q = p - 0.5; r = q * q;
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1);
  }

  // regularized incomplete beta I_x(a, b) by its continued fraction (Abramowitz & Stegun, 1964, 26.5.8), which
  // converges fast below x = (a + 1) / (a + b + 2); above it I_x(a, b) = 1 − I_(1−x)(b, a)
  function incompleteBeta(a, b, x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    return x < (a + 1) / (a + b + 2) ? betaFraction(a, b, x) : 1 - betaFraction(b, a, 1 - x);
  }
  function betaFraction(a, b, x) {
    var front = Math.exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * Math.log(x) + b * Math.log(1 - x));
    // the partial numerators: d(2k) = k(b − k)x / ((a + 2k − 1)(a + 2k)), d(2k + 1) = −(a + k)(a + b + k)x / ((a + 2k)(a + 2k + 1))
    function coefficient(j) {
      var k = Math.floor(j / 2);
      return j % 2 ? -(a + k) * (a + b + k) * x / ((a + 2 * k) * (a + 2 * k + 1))
                   : k * (b - k) * x / ((a + 2 * k - 1) * (a + 2 * k));
    }
    return front / a * fraction(function (n) { return [coefficient(n - 1), 1]; }, 6001, 1e-15);
  }

  // the x with f(x) = p for an increasing f on [lo, hi] (bisection)
  function invert(f, p, lo, hi) {
    for (var i = 0; i < 200 && hi - lo > 1e-14 * Math.max(1, Math.abs(hi)); i++) {
      var mid = (lo + hi) / 2;
      if (f(mid) < p) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }

  // upper quantile of the central t distribution: P(T > c) = tail, tail < 0.5
  function qtUpper(tail, df) {
    // P(T > c) = I_{df/(df+c²)}(df/2, 1/2) / 2; solve for y = df/(df+c²)
    var y = invert(function (y) { return incompleteBeta(df / 2, 0.5, y) / 2; }, tail, 0, 1);
    return Math.sqrt(df * (1 - y) / y);
  }

  // upper quantile of the central F distribution: P(F > c) = tail
  function qfUpper(tail, d1, d2) {
    var y = invert(function (y) { return incompleteBeta(d1 / 2, d2 / 2, y); }, 1 - tail, 0, 1);
    return d2 * y / (d1 * (1 - y));
  }

  // P(T' <= t) of the noncentral t distribution for t >= 0, any delta (after Lenth, 1989, summed in full)
  function ptNoncentralAt(t, df, delta) {
    var x = t * t / (t * t + df), lam = delta * delta / 2, sum = 0, j, lp, lq, last = Math.ceil(lam + 12 * Math.sqrt(lam) + 60);
    for (j = 0; j <= last; j++) {
      lp = -lam + (j ? j * Math.log(lam) : 0) - lgamma(j + 1);
      lq = -lam + (j ? j * Math.log(lam) : 0) - lgamma(j + 1.5) + Math.log(Math.abs(delta) || 1) - 0.5 * Math.LN2;
      sum += Math.exp(lp) * incompleteBeta(j + 0.5, df / 2, x);
      if (delta) sum += (delta > 0 ? 1 : -1) * Math.exp(lq) * incompleteBeta(j + 1, df / 2, x);
    }
    return Math.min(1, Math.max(0, pnorm(-delta) + sum / 2));
  }

  // power of a t-test with df degrees of freedom and noncentrality delta > 0
  function tPower(df, delta, alpha, onesided) {
    var c = qtUpper(onesided ? alpha : alpha / 2, df);
    var up = 1 - ptNoncentralAt(c, df, delta);
    return onesided ? up : up + (1 - ptNoncentralAt(c, df, -delta));
  }

  // P(F' > x) of the noncentral F distribution (Poisson mixture of central F)
  function pfNoncentralUpper(x, d1, d2, lambda) {
    var y = d2 / (d2 + d1 * x), half = lambda / 2, sum = 0, j, last = Math.ceil(half + 12 * Math.sqrt(half) + 60);
    for (j = 0; j <= last; j++)
      sum += Math.exp(-half + (j ? j * Math.log(half) : 0) - lgamma(j + 1)) * incompleteBeta(d2 / 2, d1 / 2 + j, y);
    return Math.min(1, sum);
  }

  // power of the one-way ANOVA with g groups of n persons each, effect size f (Cohen 1988)
  function anovaPower(n, g, f, alpha) {
    var N = n * g, d1 = g - 1, d2 = N - g;
    return pfNoncentralUpper(qfUpper(alpha, d1, d2), d1, d2, f * f * N);
  }

  // density of the sample correlation r of n pairs from a bivariate normal population with correlation rho
  function rDensityLog(r, n, rho) {
    var z = (1 + rho * r) / 2, c = n - 0.5, term = 1, sum = 1, k;
    for (k = 0; k < 200000; k++) {
      term *= (k + 0.5) * (k + 0.5) / ((c + k) * (k + 1)) * z;
      sum += term;
      if (term < sum * 1e-16) break;
    }
    return Math.log(n - 2) + lgamma(n - 1) - lgamma(n - 0.5) - 0.5 * Math.log(2 * Math.PI) +
      (n - 1) / 2 * Math.log(1 - rho * rho) - (n - 1.5) * Math.log(1 - rho * r) + Math.log(sum);
  }

  // P(a < r < b) for a sample correlation; with r = sin θ the factor (1 - r²)^((n-4)/2) dr becomes cos^(n-3) θ dθ
  function rProbability(a, b, n, rho) {
    var t0 = Math.asin(a), t1 = Math.asin(b), steps = 4000, h = (t1 - t0) / steps, sum = 0, i, th, cs, val;
    for (i = 0; i <= steps; i++) {
      th = t0 + i * h; cs = Math.cos(th);
      val = cs > 0 ? Math.exp(rDensityLog(Math.sin(th), n, rho) + (n - 3) * Math.log(cs)) : 0;
      sum += val * (i === 0 || i === steps ? 1 : i % 2 ? 4 : 2);
    }
    return sum * h / 3;
  }

  // exact power of the test of H0: rho = 0 (t-test of r with n - 2 degrees of freedom)
  function corrPower(n, rho, alpha, onesided) {
    var c = qtUpper(onesided ? alpha : alpha / 2, n - 2), rc = c / Math.sqrt(c * c + n - 2);
    var up = rProbability(rc, 1, n, rho);
    return onesided ? up : up + rProbability(-1, -rc, n, rho);
  }

  // the smallest whole n >= lo with power(n) >= target (power grows with n)
  function smallest(power, target, lo) {
    var hi = lo;
    if (power(lo) >= target) return lo;
    while (power(hi) < target) {
      lo = hi; hi *= 2;
      if (hi > 1e7) return Infinity;
    }
    while (hi - lo > 1) {
      var mid = Math.floor((lo + hi) / 2);
      if (power(mid) >= target) hi = mid; else lo = mid;
    }
    return hi;
  }

  // power of an F test with d1, d2 degrees of freedom (may be fractional) and noncentrality lambda
  function fPower(d1, d2, lambda, alpha) {
    return pfNoncentralUpper(qfUpper(alpha, d1, d2), d1, d2, lambda);
  }

  // upper quantile of the central chi-square distribution: P(X > c) = tail
  function qchisqUpper(tail, df) {
    return invert(function (x) { return 1 - upperGamma(df / 2, x / 2); }, 1 - tail, 0, df + 40 * Math.sqrt(2 * df) + 100);
  }

  // P(X' > x) of the noncentral chi-square distribution (Poisson mixture of central chi-square)
  function pchisqNoncentralUpper(x, df, lambda) {
    var half = lambda / 2, sum = 0, j, last = Math.ceil(half + 12 * Math.sqrt(half) + 60);
    for (j = 0; j <= last; j++)
      sum += Math.exp(-half + (j ? j * Math.log(half) : 0) - lgamma(j + 1)) * upperGamma(df / 2 + j, x / 2);
    return Math.min(1, sum);
  }

  // the test of close fit of a structural equation model (MacCallum, Browne & Sugawara 1996): H0 RMSEA = eps0
  // rejected when the model in truth has eps1; noncentrality (N - 1) df eps²; as sem.rmsea_power in Python
  function qchisqNoncentralUpper(tail, df, lambda) {
    return invert(function (x) { return 1 - pchisqNoncentralUpper(x, df, lambda); }, 1 - tail, 0,
                  df + lambda + 20 * Math.sqrt(2 * (df + 2 * lambda)) + 100);
  }
  function rmseaPower(N, df, eps0, eps1, alpha) {
    if (df <= 0 || N <= 1) return 0;
    var crit = qchisqNoncentralUpper(alpha, df, (N - 1) * df * eps0 * eps0);
    return pchisqNoncentralUpper(crit, df, (N - 1) * df * eps1 * eps1);
  }

  // power of the chi-square test with df degrees of freedom, effect size w, N persons (Cohen 1988: lambda = w² N)
  function chisqPower(N, df, w, alpha) {
    return pchisqNoncentralUpper(qchisqUpper(alpha, df), df, w * w * N);
  }

  // ---- the planners ------------------------------------------------------------------------------------------
  // two independent groups: n1 persons in the first, n2 = ratio · n1 (rounded up) in the second
  function groupSizes(n1, ratio) { return [n1, Math.max(2, Math.ceil(ratio * n1 - 1e-9))]; }
  function twoGroupPower(n1, d, alpha, onesided, ratio) {
    var s = groupSizes(n1, ratio || 1);
    return tPower(s[0] + s[1] - 2, d * Math.sqrt(s[0] * s[1] / (s[0] + s[1])), alpha, onesided);
  }
  // first group (equal groups: per group), d = Cohen's d
  function nTwoGroups(d, alpha, power, onesided, ratio) {
    return smallest(function (n) { return twoGroupPower(n, d, alpha, onesided, ratio); }, power, 2);
  }
  // persons measured twice, dz = mean difference / SD of the differences
  function nPaired(dz, alpha, power, onesided) {
    return smallest(function (n) { return tPower(n - 1, dz * Math.sqrt(n), alpha, onesided); }, power, 2);
  }
  // d of two measurements with correlation rho as d_z (Lakens 2013, formula 7)
  function dzFromD(d, rho) { return d / Math.sqrt(2 * (1 - rho)); }
  function nCorrelation(r, alpha, power, onesided) {
    return smallest(function (n) { return corrPower(n, r, alpha, onesided); }, power, 4);
  }
  // per group (g groups of equal size)
  function nAnova(f, g, alpha, power) {
    return smallest(function (n) { return anovaPower(n, g, f, alpha); }, power, 2);
  }

  // z-test of two proportions without continuity correction, variance under H0 pooled (Fleiss, Levin & Paik
  // 2003, ch. 4): power for n1 and n2 persons
  function propsPower(n1, n2, p1, p2, alpha, onesided) {
    var pbar = (n1 * p1 + n2 * p2) / (n1 + n2), z = qnorm(1 - (onesided ? alpha : alpha / 2));
    var se0 = Math.sqrt(pbar * (1 - pbar) * (1 / n1 + 1 / n2)), se1 = Math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2);
    var diff = Math.abs(p1 - p2), upper = pnorm((diff - z * se0) / se1);
    return onesided ? upper : Math.min(1, upper + pnorm((-diff - z * se0) / se1));
  }
  // Cohen's h of two proportions (Cohen 1988, ch. 6)
  function cohenH(p1, p2) { return Math.abs(2 * Math.asin(Math.sqrt(p1)) - 2 * Math.asin(Math.sqrt(p2))); }

  // fixed effects ANOVA with `cells` equal cells (Versuchsbedingungen) of n persons, an effect with df1 degrees of freedom (main effect
  // or interaction): lambda = f² N, df2 = N - cells (Cohen 1988, ch. 8; Faul et al. 2007)
  function factorialPower(n, cells, df1, f, alpha) {
    var N = n * cells;
    return fPower(df1, N - cells, f * f * N, alpha);
  }

  // repeated measures ANOVA after Faul et al. (2007), with sphericity correction epsilon: N persons in k groups, m measurements correlated
  // rho, sphericity epsilon. Within and interaction: lambda = f² N m eps / (1 - rho), df1 = (m - 1) eps (times
  // k - 1), df2 = (N - k)(m - 1) eps; between: lambda = f² N m / (1 + (m - 1) rho), df1 = k - 1, df2 = N - k.
  function rmPower(N, k, m, rho, eps, f, effect, alpha) {
    if (!(rho > -1 / (m - 1) && rho < 1)) return NaN;
    if (effect === "between") return fPower(k - 1, N - k, f * f * N * m / (1 + (m - 1) * rho), alpha);
    return fPower((effect === "inter" ? k - 1 : 1) * (m - 1) * eps, (N - k) * (m - 1) * eps,
      f * f * N * m * eps / (1 - rho), alpha);
  }

  // F test of R² (u = p) or of the increase by u of p predictors: lambda = f² N, df = u, N - p - 1 (Faul et al. 2009)
  function regPower(N, p, u, f2, alpha) { return fPower(u, N - p - 1, f2 * N, alpha); }

  // equivalence test with two one-sided tests at alpha each (Schuirmann 1987; Lakens 2017), bounds ± bound and
  // true difference diff (both in units of d), scale = noncentrality of a difference of 1. The power is bounded
  // below by 1 - P(lower test fails) - P(upper test fails), which errs on the safe side.
  function tostPower(df, scale, bound, diff, alpha) {
    var c = qtUpper(alpha, df);
    return Math.max(0, 1 - ptNoncentralAt(c, df, (bound + diff) * scale) - ptNoncentralAt(c, df, (bound - diff) * scale));
  }

  // half the width of the Wilson interval of a proportion p from n persons (Wilson 1927; Newcombe 1998)
  function wilsonHalf(n, p, z) {
    return z / (1 + z * z / n) * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n));
  }
  // finite population correction (Cochran 1977): n of a population of N for n0 of an endless one, and back
  function finite(n0, N) { return N ? n0 / (1 + (n0 - 1) / N) : n0; }
  function infinite(n, N) { return N && n < N ? n * (N - 1) / (N - n) : (N ? Infinity : n); }

  // Expected sample SD (denominator n - 1). The finite population correction reduces the SE, not the
  // degrees of freedom. With a design effect, n/deff is the same approximate effective n used elsewhere.
  function meanHalf(n, sd, conf, N, deff) {
    deff = deff || 1;
    if (N && n >= N) return 0;
    var effective = n / deff;
    if (n < 2 || effective < 2) return Infinity;
    return qtUpper((1 - conf) / 2, effective - 1) * sd / Math.sqrt(effective) *
      Math.sqrt(N ? (N - n) / N : 1);
  }

  function meanNeeded(sd, margin, conf, N, deff) {
    return smallest(function (n) { return meanHalf(n, sd, conf, N, deff) <= margin ? 1 : 0; }, 1, 1);
  }

  // Bonett (2002): persons to test coefficient alpha of k items, H0 alpha0 against alpha1
  function nAlpha(k, a0, a1, alpha, power, onesided) {
    var z = qnorm(1 - (onesided ? alpha : alpha / 2)) + qnorm(power), l = Math.log((1 - a0) / (1 - a1));
    return Math.ceil(2 * k / (k - 1) * z * z / (l * l) + 2);
  }

  // Kish: design effect of clusters of mean size m with intraclass correlation icc (1 + (m - 1) icc, Kish 1965)
  // times that of weights with coefficient of variation cv (1 + cv², Kish 1992; product: Gabler et al. 1999).
  // null: an input out of range.
  function designEffect(m, icc, cv) {
    var deff = 1;
    if (given(m) || given(icc)) {
      if (!(given(m) && given(icc))) return 1;          // one of the two still missing: no correction yet
      if (!(m >= 1 && icc >= 0 && icc < 1)) return null;
      deff *= 1 + (m - 1) * icc;
    }
    if (given(cv)) {
      if (!(cv >= 0 && cv <= 10)) return null;
      deff *= 1 + cv * cv;
    }
    return deff;
  }

  function fixed(x, digits) {
    return x.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function num(n) { return n.toLocaleString(LOCALE); }
  // α with two decimals, three where Holm's α / k needs them (0,025, 0,017)
  // one term and one form for each figure on all pages (UI/UX audit 2026-10-07, W3): "α = ,05" as APA writes it,
  // "Power 80 %" as the research plan
  function alphaText(a) { return fixed(a, Math.abs(a * 100 - Math.round(a * 100)) < 1e-9 ? 2 : 3).replace(/^0(?=[.,])/, ""); }
  function powerText(p) { return Math.round(p * 100) + de_en(" %", "%"); }
  function given(x) { return typeof x === "number" && isFinite(x); }
  function pct(p) { return fixed(p * 100, 1) + de_en(" %", "%"); }
  function within(x, lo, hi) { return given(x) && x >= lo && x <= hi; }

  // The statistical tests of the page. Each knows the power for n units (a unit: the first group, a group, a
  // cell or a person) and an effect, the persons of n units, the units of N persons and the text of the result.
  // null: an input is missing or out of range.
  function testSpec(design, v) {
    var a = v.alpha, os = !!v.onesided, ratio = given(v.ratio) ? v.ratio : 1;
    if (!(a > 0 && a < 0.5)) return null;
    function equal(g, what) {
      return { total: function (n) { return n * g; }, unit: function (N) { return Math.floor(N / g); },
               text: function (n) { return num(n) + " " + de_en("je", "per") + " " + what[0] + " × " + g + " " + what[1]; } };
    }
    function spec(base, more) { for (var k in more) base[k] = more[k]; return base; }
    var sides = os ? de_en(" einseitig", " one-sided") : de_en(" zweiseitig", " two-sided");
    var GROUP = [de_en("Gruppe", "group"), de_en("Gruppen", "groups")];
    function perGroup(s) {
      return s[0] === s[1] ? de_en(num(s[0]) + " je Gruppe × 2 Gruppen", num(s[0]) + " per group × 2 groups")
        : de_en(num(s[0]) + " + " + num(s[1]) + " Personen (Verhältnis 1 : " + fixed(ratio, 2) + ")",
                num(s[0]) + " + " + num(s[1]) + " persons (ratio 1 : " + fixed(ratio, 2) + ")");
    }
    if (design === "groups") {
      if (!within(ratio, 0.1, 10)) return null;
      return { lo: 2, effect: v.effect, valid: within(v.effect, 0.01, 5), range: [0.001, 10], name: "d", digits: 2, sides: sides,
        power: function (n, d) { return twoGroupPower(n, d, a, os, ratio); },
        total: function (n) { var s = groupSizes(n, ratio); return s[0] + s[1]; },
        unit: function (N) { var n = Math.floor(N / (1 + ratio)); while (n > 2 && groupSizes(n, ratio)[0] + groupSizes(n, ratio)[1] > N) n--; return n; },
        text: function (n) { return perGroup(groupSizes(n, ratio)); } };
    }
    if (design === "props") {
      var p1 = v.p1 / 100, p2 = v.p2 / 100;
      if (!within(ratio, 0.1, 10) || !within(p1, 0.001, 0.999)) return null;
      return { lo: 2, effect: p2, valid: within(p2, 0.001, 0.999) && Math.abs(p2 - p1) >= 0.001, range: [p1 + 1e-6, 1 - 1e-6], sides: sides,
        power: function (n, q) { var s = groupSizes(n, ratio); return propsPower(s[0], s[1], p1, q, a, os); },
        total: function (n) { var s = groupSizes(n, ratio); return s[0] + s[1]; },
        unit: function (N) { var n = Math.floor(N / (1 + ratio)); while (n > 2 && groupSizes(n, ratio)[0] + groupSizes(n, ratio)[1] > N) n--; return n; },
        text: function (n) { return perGroup(groupSizes(n, ratio)); },
        show: function (q) { return pct(q) + de_en(" gegen ", " vs. ") + pct(p1) + " (h = " + fixed(cohenH(p1, q), 2) + ")"; } };
    }
    if (design === "anova") {
      var g = Math.round(v.groups);
      if (!within(g, 2, 50)) return null;
      return spec({ lo: 2, effect: v.f, valid: within(v.f, 0.01, 5), range: [0.001, 10], name: "f", digits: 2, sides: "",
        power: function (n, f) { return anovaPower(n, g, f, a); } }, equal(g, GROUP));
    }
    if (design === "factorial") {
      var fa = Math.round(v.fa), fb = Math.round(v.fb), df1 = v.factor === "B" ? fb - 1 : v.factor === "AB" ? (fa - 1) * (fb - 1) : fa - 1;
      if (!within(fa, 2, 10) || !within(fb, 2, 10)) return null;
      var label = v.factor === "B" ? de_en("Haupteffekt B", "main effect B") : v.factor === "AB" ? de_en("Wechselwirkung A × B", "interaction A × B") : de_en("Haupteffekt A", "main effect A");
      return spec({ lo: 2, effect: v.f, valid: within(v.f, 0.01, 5), range: [0.001, 10], name: "f", digits: 2, sides: "",
        power: function (n, f) { return factorialPower(n, fa * fb, df1, f, a); }, extra: label + " (" + fa + " × " + fb + ")" },
        equal(fa * fb, [de_en("Versuchsbedingung", "condition"), de_en("Versuchsbedingungen", "conditions")]));
    }
    if (design === "chi2") {
      var rows = Math.round(v.rows), cols = Math.round(v.cols), df = cols === 1 ? rows - 1 : (rows - 1) * (cols - 1);
      if (!within(rows, 2, 30) || !within(cols, 1, 30)) return null;
      return { lo: df + 2, effect: v.w, valid: within(v.w, 0.01, 3), range: [0.001, 5], name: "w", digits: 2, sides: "",
        power: function (N, w) { return chisqPower(N, df, w, a); },
        total: function (N) { return N; }, unit: function (N) { return N; },
        text: function () { return (cols === 1 ? de_en("Verteilung über " + rows + " Kategorien", "distribution over " + rows + " categories") : de_en(rows + " × " + cols + "-Tabelle", rows + " × " + cols + " table")) + ", df = " + df; } };
    }
    if (design === "equiv") {
      var paired = v.eqdesign === "paired", diff = given(v.truediff) ? Math.abs(v.truediff) : 0;
      if (paired && !within(v.rho, -0.95, 0.95)) return null;
      var unitD = paired ? 1 / Math.sqrt(2 * (1 - v.rho)) : 1;    // d into d_z (Lakens 2013)
      if (!within(diff, 0, 5)) return null;
      return { lo: 2, effect: v.bound, valid: within(v.bound, 0.01, 5) && v.bound > diff, range: [diff + 1e-4, 10], sides: "",
        power: function (n, b) {
          return tostPower(paired ? n - 1 : 2 * n - 2, paired ? Math.sqrt(n) : Math.sqrt(n / 2), b * unitD, diff * unitD, a);
        },
        total: function (n) { return paired ? n : 2 * n; }, unit: function (N) { return paired ? N : Math.floor(N / 2); },
        text: function (n) { return paired ? de_en("jede Person in beiden Bedingungen", "every person in both conditions") : perGroup([n, n]); },
        show: function (b) { return de_en("Grenzen ± ", "bounds ± ") + fixed(b, 2) + " (d)"; },
        extra: diff ? de_en("wahrer Unterschied d = ", "true difference d = ") + fixed(diff, 2) : "", tost: true };
    }
    if (design === "paired") {
      if (!within(v.rho, -0.95, 0.95)) return null;
      return { lo: 2, effect: v.effect, valid: within(v.effect, 0.01, 5), range: [0.001, 10], name: "d", digits: 2, sides: sides,
        power: function (n, d) { return tPower(n - 1, dzFromD(d, v.rho) * Math.sqrt(n), a, os); },
        total: function (n) { return n; }, unit: function (N) { return N; },
        text: function () { return de_en("jede Person in beiden Versuchsbedingungen", "every person in both conditions"); },
        extra: "ρ = " + fixed(v.rho, 2), dz: true };
    }
    if (design === "rm") {
      var m = Math.round(v.meas), k = Math.round(v.rmgroups), eps = given(v.eps) ? v.eps : 1, eff = v.rmeffect || "within";
      if (!within(m, 2, 20) || !within(k, 1, 20) || !within(v.rho, -0.95, 0.95) || !within(eps, 1 / (m - 1) - 1e-9, 1)) return null;
      if (v.rho <= -1 / (m - 1)) return null;
      if (eff !== "within" && k < 2) return null;
      var what = eff === "between" ? de_en("Gruppen", "groups") : eff === "inter" ? de_en("Wechselwirkung Zeit × Gruppe", "interaction time × group") : de_en("Zeit (innerhalb der Personen)", "time (within people)");
      return spec({ lo: 2, effect: v.f, valid: within(v.f, 0.01, 5), range: [0.001, 10], name: "f", digits: 2, sides: "",
        power: function (n, f) { return rmPower(n * k, k, m, v.rho, eps, f, eff, a); },
        extra: de_en("Effekt " + what + ", " + m + " Messungen, ρ = ", "effect " + what + ", " + m + " measurements, ρ = ") + fixed(v.rho, 2) + (eps < 1 ? ", ε = " + fixed(eps, 2) : "") },
        k === 1 ? { total: function (n) { return n; }, unit: function (N) { return N; }, text: function () { return de_en("jede Person " + m + "-mal gemessen", "every person measured " + m + " times"); } }
                : equal(k, GROUP));
    }
    if (design === "corr") {
      return { lo: 4, effect: v.effect, valid: within(v.effect, 0.01, 0.99), range: [0.001, 0.99], name: "r", digits: 2, sides: sides,
        power: function (n, r) { return corrPower(n, r, a, os); },
        total: function (n) { return n; }, unit: function (N) { return N; }, text: function () { return de_en("Paare von Messungen", "pairs of measurements"); } };
    }
    if (design === "reg") {
      var p = Math.round(v.preds), u = given(v.tested) ? Math.round(v.tested) : p;
      if (!within(p, 1, 50) || !within(u, 1, p)) return null;
      var inc = u < p && given(v.dr2), f2 = inc ? v.dr2 / (1 - v.r2) : v.r2 / (1 - v.r2);
      return { lo: p + 2, effect: f2, valid: within(v.r2, 0.001, 0.99) && (!inc || within(v.dr2, 0.001, v.r2)) && f2 > 0, range: [0.0001, 10], name: "f²", digits: 3, sides: "",
        power: function (N, e) { return regPower(N, p, u, e, a); },
        total: function (N) { return N; }, unit: function (N) { return N; },
        text: function () { return de_en(p + " Prädiktoren" + (u < p ? ", davon " + u + " getestet" : ""), p + " predictors" + (u < p ? ", " + u + " of them tested" : "")); },
        show: function (e) { return "f² = " + fixed(e, 3) + (u === p ? " (R² = " + fixed(e / (1 + e), 3) + ")" : ""); } };
    }
    return null;
  }
  var TESTS = ["groups", "props", "anova", "factorial", "chi2", "equiv", "paired", "rm", "corr", "reg"];
  var ESTIMATES = ["prop", "mean"];

  function effectText(s, e) { return s.show ? s.show(e) : s.name + " = " + fixed(e, s.digits); }
  function testLine(s, v) {
    return " · α = " + alphaText(v.alpha) + (s.tost ? de_en(" je einseitigem Test", " per one-sided test") : s.sides) + de_en(", Power ", ", power ") + powerText(v.power);
  }

  // completions needed: {base, detail, warning} before design effect and subgroup, or null. The estimates also give
  // n0 (before the finite population correction) and pop: plan() corrects for the population only after the
  // design effect and the subgroup (Kish 1965; Cochran 1977)
  function sample(design, v) {
    var warning = "", N, z, n;
    if (TESTS.indexOf(design) >= 0) {
      var s = testSpec(design, v);
      if (!s || !s.valid || !(v.power > 0 && v.power < 1)) return null;
      n = smallest(function (n) { return s.power(n, s.effect); }, v.power, s.lo);
      if (!isFinite(n)) return null;
      var line = s.text(n) + ", " + effectText(s, s.effect);
      if (s.dz) line += de_en(" bei " + s.extra + " ergibt d_z = ", " at " + s.extra + " gives d_z = ") + fixed(dzFromD(s.effect, v.rho), 2);
      else if (s.extra) line += " · " + s.extra;
      return { base: s.total(n), detail: line + testLine(s, v), warning: "" };
    }
    if (design === "prop") {
      var p = v.p / 100, m = v.margin / 100;
      N = given(v.pop) && v.pop > 0 ? Math.floor(v.pop) : 0;
      if (!(within(p, 0.001, 0.999) && m > 0 && m < 0.5 && v.conf > 0 && v.conf < 1) || (given(v.pop) && v.pop < 0)) return null;
      z = qnorm(1 - (1 - v.conf) / 2);
      n = smallest(function (n) { return wilsonHalf(n, p, z) <= m ? 1 : 0; }, 1, 1);
      if (!isFinite(n)) return null;
      return { base: Math.ceil(finite(n, N) - 1e-9), n0: n,
        detail: de_en("Fehlerspanne ± " + num(v.margin) + " Prozentpunkte bei " + Math.round(v.conf * 100) + " % Vertrauen (Wilson-Intervall)",
                      "margin of error ± " + num(v.margin) + " percentage points at " + Math.round(v.conf * 100) + "% confidence (Wilson interval)") +
          (N ? de_en(", Grundgesamtheit ", ", population ") + num(N) : ""), warning: "", pop: N };
    }
    if (design === "mean") {
      N = given(v.pop) && v.pop > 0 ? Math.floor(v.pop) : 0;
      if (!(v.sd > 0 && v.emargin > 0 && v.conf > 0 && v.conf < 1) || (given(v.pop) && v.pop < 0)) return null;
      n = meanNeeded(v.sd, v.emargin, v.conf, N, 1);
      if (!isFinite(n)) return null;
      return { base: n,
        detail: de_en("Mittelwert ± " + num(v.emargin) + " bei Standardabweichung " + num(v.sd) + " und " + Math.round(v.conf * 100) + " % Vertrauen",
                      "mean ± " + num(v.emargin) + " at standard deviation " + num(v.sd) + " and " + Math.round(v.conf * 100) + "% confidence") +
          (N ? de_en(", Grundgesamtheit ", ", population ") + num(N) : ""), warning: "", pop: N };
    }
    if (design === "alpha") {
      var k = Math.round(v.items);
      if (!(within(k, 2, 200) && within(v.alpha0, 0.01, 0.99) && within(v.alpha1, 0.01, 0.99) && Math.abs(v.alpha1 - v.alpha0) >= 0.005 &&
            v.alpha > 0 && v.alpha < 0.5 && v.power > 0 && v.power < 1)) return null;
      return { base: nAlpha(k, v.alpha0, v.alpha1, v.alpha, v.power, v.onesided),
        detail: k + de_en(" Items, α₀ = ", " items, α₀ = ") + fixed(v.alpha0, 2) + de_en(" gegen α₁ = ", " vs. α₁ = ") + fixed(v.alpha1, 2) +
          de_en(" (Bonett 2002) · Signifikanzniveau ", " (Bonett 2002) · significance level ") + alphaText(v.alpha) +
          (v.onesided ? de_en(" einseitig", " one-sided") : de_en(" zweiseitig", " two-sided")) + de_en(", Power ", ", power ") + powerText(v.power), warning: "" };
    }
    if (design === "sem") {
      var sdf = Math.round(v.semdf);
      if (!(within(sdf, 1, 5000) && within(v.eps0, 0, 0.2) && within(v.eps1, 0.01, 0.3) && v.eps1 > v.eps0 &&
            v.alpha > 0 && v.alpha < 0.5 && v.power > 0 && v.power < 1)) return null;
      var semN = smallest(function (n) { return rmseaPower(n, sdf, v.eps0, v.eps1, v.alpha); }, v.power, 2);
      if (!isFinite(semN)) return null;
      return { base: semN,
        detail: "df = " + sdf + de_en(", RMSEA ", ", RMSEA ") + fixed(v.eps0, 2) + de_en(" gegen ", " vs. ") + fixed(v.eps1, 2) +
          de_en(" (MacCallum et al. 1996) · Signifikanzniveau ", " (MacCallum et al. 1996) · significance level ") + alphaText(v.alpha) +
          de_en(", Power ", ", power ") + powerText(v.power), warning: "" };
    }
    if (design === "conjoint") {
      if (!(v.levels > 0 && v.tasks > 0 && v.alts > 0)) return null;
      var raw = 500 * v.levels / (v.tasks * v.alts);
      return { base: Math.ceil(raw * 1.5), detail: de_en("Minimum " + num(Math.ceil(raw)) + " (Orme 2010), empfohlen das 1,5-Fache (Festlegung von Askurious)",
                                                         "minimum " + num(Math.ceil(raw)) + " (Orme 2010), 1.5 times that recommended (set by Askurious)"), warning: "" };
    }
    if (design === "maxdiff") {
      var K = Math.round(v.mdk), size = Math.round(v.mdset), sets = Math.round(v.mdsets);
      if (!(within(K, 4, 300) && within(size, 2, K - 1) && within(sets, 1, 100))) return null;
      var least = Math.ceil(3 * K / size);
      if (sets < least)
        warning = de_en("Für Werte je Person (Hierarchical Bayes) sollte jede Aussage etwa dreimal je Person vorkommen: mindestens " +
          least + " Sets (Festlegung von Askurious).", "For scores per person (Hierarchical Bayes) every statement should appear about three times " +
          "per person: at least " + least + " sets (Askurious default).");
      return { base: Math.ceil(1000 * K / (sets * size)),
        detail: de_en("jede Aussage 1.000-mal gezeigt; Minimum " + num(Math.ceil(500 * K / (sets * size))) + " für 500-mal (Festlegung von Askurious nach Orme, 2010)",
                      "every statement shown 1,000 times; minimum " + num(Math.ceil(500 * K / (sets * size))) + " for 500 times (Askurious default after Orme, 2010)"),
        warning: warning };
    }
    if (design === "pairedcmp") {
      // the Bradley-Terry model at values 0 (de Bekker-Grob et al., 2015): variance 2 (K - 1)^2 / (P K) per person of a
      // zero-centred value when the P pairs of a person are spread evenly over the K (K - 1) / 2 pairs
      var Kp = Math.round(v.pck), Pp = Math.round(v.pcp);
      if (!(within(Kp, 3, 20) && within(Pp, 1, Kp * (Kp - 1) / 2) && v.pcdelta > 0 && v.alpha > 0 && v.alpha < 0.5 &&
            v.power > 0 && v.power < 1)) return null;
      var zp = qnorm(1 - v.alpha / 2) + qnorm(v.power), varp = 2 * (Kp - 1) * (Kp - 1) / (Pp * Kp);
      if (2 * Pp < 3 * Kp && Pp < Kp * (Kp - 1) / 2)
        warning = de_en("Für Werte je Person (Hierarchical Bayes) sollte jede Option etwa dreimal je Person vorkommen: mindestens " +
          Math.ceil(1.5 * Kp) + " Paare.", "For values per person (Hierarchical Bayes) every option should appear about three times " +
          "per person: at least " + Math.ceil(1.5 * Kp) + " pairs.");
      return { base: Math.ceil(zp * zp * varp / (v.pcdelta * v.pcdelta) - 1e-9),
        detail: de_en("jeder Wert mit dieser Power von 0 verschieden (de Bekker-Grob et al., 2015); Varianz je Person " + fixed(varp, 3),
                      "every value differs from 0 with this power (de Bekker-Grob et al., 2015); variance per person " + fixed(varp, 3)),
        warning: warning };
    }
    if (design === "vignette") {
      var V = Math.round(v.vtotal), per = Math.round(v.vper), deck = Math.round(v.vdeck);
      if (!(within(V, 1, 100000) && within(per, 1, V) && within(deck, 1, 1000))) return null;
      var decks = Math.ceil(V / per), total = decks * deck;
      if (per > 10)
        warning = de_en("Mehr als 10 Vignetten je Person sind für allgemeine Bevölkerungsstichproben nicht zu empfehlen " +
          "(Sauer et al., 2011; Auspurg & Hinz, 2015).", "More than 10 vignettes per person are not advisable for general " +
          "population samples (Sauer et al., 2011; Auspurg & Hinz, 2015).");
      var rated = fixed(total * per / V, 1).replace(/[,.]0$/, "");
      return { base: total, detail: de_en(decks + " Decks × " + deck + " Personen je Deck; jede Vignette etwa " + rated + "-mal bewertet",
                                          decks + " decks × " + deck + " people per deck; every vignette rated about " + rated + " times"), warning: warning };
    }
    return null;
  }

  // the smallest effect that n completions detect: {text, detail, warning}, or null. eff: for the estimates the
  // completions as from a simple random sample of an infinite population, if plan() has taken them already (the
  // population, the subgroup and the design effect in the right order)
  function sensitivity(design, v, n, eff, meanDesign) {
    var z, N;
    if (TESTS.indexOf(design) >= 0) {
      var s = testSpec(design, v);
      if (!s || !(v.power > 0 && v.power < 1)) return null;
      var unit = s.unit(n);
      if (unit < s.lo) return { text: "–", detail: "", warning: de_en("Zu wenige Teilnahmen für diesen Test.", "Too few participations for this test.") };
      var f = function (e) { return s.power(unit, e); };
      if (f(s.range[1]) < v.power) return { text: "–", detail: "", warning: de_en("Mit so wenigen Teilnahmen ist die Power nicht erreichbar.", "With so few participations the power cannot be reached.") };
      var e = invert(f, v.power, s.range[0], s.range[1]);
      var line = s.text(unit) + (s.extra && !s.dz ? " · " + s.extra : s.dz ? de_en(" bei ", " at ") + s.extra : "");
      return { text: effectText(s, e), detail: line + testLine(s, v), warning: "" };
    }
    if (design === "prop" || design === "mean") {
      N = given(v.pop) && v.pop > 0 ? Math.floor(v.pop) : 0;
      if (design === "mean") {
        if (!(v.sd > 0 && v.conf > 0 && v.conf < 1) || (given(v.pop) && v.pop < 0)) return null;
        var md = meanDesign || { n: n, pop: N, deff: 1 };
        var half = meanHalf(md.n, v.sd, v.conf, md.pop, md.deff);
        return { text: isFinite(half) ? "± " + fixed(half, 2) : "–",
          detail: half === 0 ? de_en("Vollerhebung der Grundgesamtheit", "full census of the population") :
            de_en("Mittelwert bei Standardabweichung " + num(v.sd) + ", " + Math.round(v.conf * 100) + " % Vertrauen",
                  "mean at standard deviation " + num(v.sd) + ", " + Math.round(v.conf * 100) + "% confidence"),
          warning: isFinite(half) ? "" : de_en("Zu wenige Teilnahmen für dieses Intervall.", "Too few participations for this interval.") };
      }
      if (!(v.conf > 0 && v.conf < 1) || n < 2) return null;
      z = qnorm(1 - (1 - v.conf) / 2);
      var n0 = eff === undefined ? infinite(n, N) : eff;
      if (!isFinite(n0)) return { text: "± 0", detail: de_en("Vollerhebung der Grundgesamtheit", "full census of the population"), warning: "" };
      if (design === "prop") {
        var p = v.p / 100;
        if (!within(p, 0.001, 0.999)) return null;
        return { text: "± " + fixed(wilsonHalf(n0, p, z) * 100, 1) + de_en(" Prozentpunkte", " percentage points"),
          detail: de_en("erwarteter Anteil " + num(v.p) + " %, " + Math.round(v.conf * 100) + " % Vertrauen (Wilson-Intervall)",
                        "expected share " + num(v.p) + "%, " + Math.round(v.conf * 100) + "% confidence (Wilson interval)"), warning: "" };
      }
    }
    return null;
  }

  // What the page shows for the fields in `v` (numbers as typed, NaN for an empty field; onesided a boolean;
  // mode, factor, rmeffect and eqdesign strings). Returns {ok, total, effect, detail, invite, warning}; ok false:
  // an input is missing or out of range, nothing to show. With mode "effect" the planners of tests and estimates
  // show the smallest effect (or the margin) that the given completions reach; total stays null.
  function plan(design, v) {
    var none = { ok: false, total: null, effect: null, detail: "", invite: "–", warning: "" };
    if (design === "rm" && within(Math.round(v.meas), 2, 20) && given(v.rho) && v.rho <= -1 / (Math.round(v.meas) - 1)) {
      none.warning = de_en("Die Korrelation muss größer als −1 / (Anzahl der Messungen − 1) sein. Sonst ist dieses Messwiederholungsdesign nicht möglich.",
                           "The correlation must be greater than −1 / (number of measurements − 1). Otherwise this repeated measures design is not possible.");
      return none;
    }
    var deff = designEffect(v.cluster, v.icc, v.cv), share = given(v.share) ? v.share / 100 : 1;
    if (deff === null || !(share > 0 && share <= 1)) return none;
    var extra = (deff > 1 + 1e-9 ? de_en(" · Designeffekt ", " · design effect ") + fixed(deff, 2) : "") +
      (share < 1 ? de_en(" · Teilgruppe mit " + num(Math.round(share * 1000) / 10) + " % der Teilnahmen",
                         " · subgroup with " + num(Math.round(share * 1000) / 10) + "% of participations") : "");
    if (v.mode === "effect" && (TESTS.indexOf(design) >= 0 || ESTIMATES.indexOf(design) >= 0)) {
      if (!(v.avail >= 1)) return none;
      var usable = Math.floor(v.avail * share / deff + 1e-9), eff;
      if (ESTIMATES.indexOf(design) >= 0) {
        // the reverse of the order below: the completions of the subgroup against its part of the population first
        // (finite correction), then the design effect
        var pop = given(v.pop) && v.pop > 0 ? Math.floor(v.pop) : 0;
        eff = infinite(Math.floor(v.avail * share + 1e-9), pop * share) / deff;
      }
      var r = sensitivity(design, v, usable, eff, { n: Math.floor(v.avail * share + 1e-9), pop: pop * share, deff: deff });
      if (!r) return none;
      if (usable !== Math.floor(v.avail)) r.detail += de_en(" · nutzbar " + num(usable) + " von " + num(Math.floor(v.avail)), " · usable " + num(usable) + " of " + num(Math.floor(v.avail))) + extra;
      return { ok: true, total: null, effect: r.text, detail: r.detail, invite: "–", warning: r.warning };
    }
    var got = sample(design, v);
    if (!got || !isFinite(got.base)) return none;
    var total, detail = got.detail, warning = got.warning;
    if (design === "mean") {
      var meanSub = meanNeeded(v.sd, v.emargin, v.conf, got.pop * share, deff);
      if (!isFinite(meanSub)) return none;
      total = Math.ceil(meanSub / share - 1e-9);
    } else if (got.n0 !== undefined) {
      // n0 · deff, then within the subgroup (its part share · N of the population) the finite population
      // correction, then up to all completions; the correction first asked for too many (N = 500, deff 3: 651
      // instead of 349, "nicht erreichbar")
      var sub = Math.ceil(finite(got.n0 * deff, got.pop * share) - 1e-9);
      total = Math.max(got.least || 0, share < 1 ? Math.ceil(sub / share - 1e-9) : sub);
    } else {
      total = Math.ceil(got.base * deff / share - 1e-9);
    }
    if (total !== got.base) detail += extra + de_en(" (ohne diese Korrekturen ", " (without these corrections ") + num(got.base) + ")";
    // whole per cent, half up, as the study plan keeps them (study_plan._percent): 12,5 % computed 96 completions
    // here while the page showed and passed on 13 %, which gives 97 in the plan (audit 2026-10-10, B-22)
    var drop = Math.min(Math.max(given(v.dropout) ? Math.round(v.dropout) : 0, 0), 90) / 100;
    // the completions as app/sample_model.completions: usable participations plus the expected exclusions (S3)
    var invite = Math.ceil(total / (1 - drop) - 1e-9), inviteText, N = got.pop;
    if (N && invite > N) {
      inviteText = num(N) + de_en(" (alle)", " (all)");
      warning = (warning ? warning + " " : "") + de_en("Bei " + Math.round(drop * 100) + " % Ausschlüssen ist das Ziel nicht " +
        "erreichbar: Selbst wenn alle " + num(N) + " teilnehmen, bleiben nur etwa " +
        num(Math.floor(N * (1 - drop))) + " verwertbare Teilnahmen.", "With " + Math.round(drop * 100) + "% exclusions the target " +
        "cannot be reached: even if all " + num(N) + " take part, only about " + num(Math.floor(N * (1 - drop))) + " usable participations remain.");
    } else {
      inviteText = num(invite) + (drop ? de_en(" (bei " + Math.round(drop * 100) + " % Ausschlüssen)", " (at " + Math.round(drop * 100) + "% exclusions)") : "");
    }
    return { ok: true, total: total, completions: N && invite > N ? N : invite, capped: !!(N && invite > N), effect: null,
             detail: detail, invite: inviteText, warning: warning };
  }

  // The power curve under the result (PA3, concept poweranalyse-konzept.md): for the tests, the power over the
  // completions for the chosen effect and, faintly, for Cohen's small, medium and large one; with "erreichbaren
  // Effekt zeigen" the power over the effect at the given completions. The completions count as in plan(): the
  // design effect and the subgroup take their share before the test sees them. Returns {mode, x, series:
  // [{label, main, pts: [[x, power]]}], marks: [{x, y, label}]}, or null where there is no test or no result.
  var COHEN_STEPS = { d: [0.2, 0.5, 0.8], f: [0.1, 0.25, 0.4], w: [0.1, 0.3, 0.5], r: [0.1, 0.3, 0.5] };
  var CURVE_POINTS = 40;
  function curve(design, v) {
    if (TESTS.indexOf(design) < 0) return null;
    var s = testSpec(design, v), r = plan(design, v);
    if (!s || !r.ok) return null;
    var deff = designEffect(v.cluster, v.icc, v.cv), share = given(v.share) ? v.share / 100 : 1;
    function at(N, e) {
      var unit = s.unit(Math.floor(N * share / deff + 1e-9));
      return unit < s.lo ? 0 : s.power(unit, e);
    }
    function name(e) { return s.name ? s.name + " = " + fixed(e, s.digits) : effectText(s, e); }
    var i, pts, out = { mode: v.mode === "effect" ? "effect" : "n", series: [], marks: [] };
    if (out.mode === "effect") {
      var N = Math.floor(v.avail), found = invert(function (e) { return at(N, e); }, v.power, s.range[0], s.range[1]);
      var top = Math.min(s.range[1], found * 2.5), bottom = Math.max(s.range[0], design === "props" ? s.range[0] : 0);
      pts = [];
      for (i = 0; i <= CURVE_POINTS; i++) { var e = bottom + (top - bottom) * i / CURVE_POINTS; pts.push([e, at(N, e)]); }
      out.x = [bottom, top];
      out.series.push({ label: de_en(num(N) + " Teilnahmen", num(N) + " participations"), main: true, pts: pts });
      // the power not reachable even at the largest effect (plan() says "nicht erreichbar"): no mark, which would
      // name the end of the range as the effect for it (G4)
      if (at(N, s.range[1]) >= v.power) out.marks.push({ x: found, y: v.power, label: name(found) + " → " + powerText(v.power), head: name(found), power: powerText(v.power) });
      return out;
    }
    var total = r.total, lo = Math.max(1, Math.ceil(s.total(s.lo) * deff / share)), hi = Math.max(total * 2, lo + 10);
    // round ticks on the axis: 1, 2 or 5 times a power of ten, at most six; the curves run to the last one
    var raw = hi / 5, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    out.step = [1, 2, 5, 10].map(function (f) { return f * mag; }).filter(function (st) { return st >= raw; })[0];
    hi = Math.ceil(hi / out.step) * out.step;
    var effects = [s.effect];
    (COHEN_STEPS[s.name] || []).forEach(function (e) { if (Math.abs(e - s.effect) > 1e-9) effects.push(e); });
    effects.forEach(function (e, k) {
      var line = [];
      for (i = 0; i <= CURVE_POINTS; i++) { var n = lo + (hi - lo) * i / CURVE_POINTS; line.push([n, at(n, e)]); }
      out.series.push({ label: s.name ? name(e) : de_en("gewählter Effekt", "chosen effect"), main: k === 0, pts: line });
    });
    out.x = [0, hi];
    out.marks.push({ x: total, y: at(total, s.effect), label: num(total) + " → " + powerText(v.power), head: num(total), power: powerText(v.power) });
    if (v.power < 0.9) {
      var more = smallest(function (n) { return at(n, s.effect) >= 0.9 ? 1 : 0; }, 1, total);
      if (isFinite(more) && more <= hi) out.marks.push({ x: more, y: at(more, s.effect), label: num(more) + " → " + powerText(0.9), minor: true });
    }
    return out;
  }

  var core = {
    lgamma: lgamma, pnorm: pnorm, qnorm: qnorm, incompleteBeta: incompleteBeta, qtUpper: qtUpper, qfUpper: qfUpper,
    ptNoncentralAt: ptNoncentralAt, tPower: tPower, anovaPower: anovaPower, corrPower: corrPower,
    rProbability: rProbability, nTwoGroups: nTwoGroups, nPaired: nPaired, dzFromD: dzFromD,
    nCorrelation: nCorrelation, nAnova: nAnova, fPower: fPower, qchisqUpper: qchisqUpper, chisqPower: chisqPower,
    propsPower: propsPower, cohenH: cohenH, factorialPower: factorialPower, rmPower: rmPower, regPower: regPower,
    tostPower: tostPower, wilsonHalf: wilsonHalf, nAlpha: nAlpha, designEffect: designEffect, plan: plan, rmseaPower: rmseaPower,
    curve: curve, meanHalf: meanHalf
  };
  if (typeof module === "object" && module.exports) module.exports = core;
  else root.QSampleSize = core;
})(typeof self !== "undefined" ? self : this);

(function () {
  if (typeof document === "undefined") return;
  var box = document.querySelector("[data-ss]");
  if (!box) return;
  var design = box.querySelector("[data-ss-design]");
  var FIELDS = ["effect", "f", "w", "rho", "alpha", "power", "onesided", "groups", "ratio", "p1", "p2", "fa", "fb", "factor",
    "rows", "cols", "bound", "truediff", "eqdesign", "meas", "rmgroups", "rmeffect", "eps", "preds", "tested", "r2", "dr2",
    "margin", "conf", "p", "pop", "sd", "emargin", "items", "alpha0", "alpha1", "tasks", "alts", "levels", "mdk", "mdset",
    "mdsets", "pck", "pcp", "pcdelta", "vtotal", "vper", "vdeck", "mode", "avail", "cluster", "icc", "cv", "share", "dropout", "semdf", "eps0", "eps1"];
  function v(name) {
    var el = box.querySelector('[data-ss="' + name + '"]');
    if (!el) return NaN;
    if (el.type === "checkbox") return el.checked;
    if (el.hasAttribute("data-ss-text")) return el.value;
    var text = String(el.value).trim().replace(",", ".");
    return text === "" ? NaN : Number(text);
  }
  function out(name, text) { box.querySelector('[data-ss-out="' + name + '"]').textContent = text; }
  function calc() {
    var d = design.value, modeEl = box.querySelector('[data-ss="mode"]');
    // the cells of the chosen planner, and below the hints that concern it; cells marked data-ss-mode only in
    // that mode (the mode field itself only for the planners that offer it)
    document.querySelectorAll("[data-ss-for]").forEach(function (el) { el.hidden = el.dataset.ssFor.split(" ").indexOf(d) < 0; });
    var mode = modeEl && !modeEl.closest("[data-ss-for]").hidden && modeEl.checked ? "effect" : "n";
    box.querySelectorAll("[data-ss-mode]").forEach(function (el) {
      var mine = !el.dataset.ssFor || el.dataset.ssFor.split(" ").indexOf(d) >= 0;
      el.hidden = !mine || el.dataset.ssMode !== mode;
    });
    var fields = {};
    FIELDS.forEach(function (name) { fields[name] = v(name); });
    fields.mode = mode;
    var r = window.QSampleSize.plan(d, fields);
    out("n", r.ok ? (r.effect !== null ? r.effect : r.total.toLocaleString(document.documentElement.lang === "en" ? "en-GB" : "de-DE")) : "–");
    // the number the test or the estimate needs: usable participations; the completions to plan for come below,
    // with the expected exclusions (UI/UX audit 2026-10-07, W2)
    out("unit", mode === "effect" ? (d === "prop" || d === "mean" ? de_en("erreichbare Fehlerspanne", "reachable margin of error") : de_en("kleinster erkennbarer Effekt", "smallest detectable effect"))
                                  : de_en("verwertbare Teilnahmen", "usable participations"));
    out("detail", r.ok ? r.detail : de_en("Bitte alle Felder ausfüllen (Zahlen größer als 0).", "Please fill in all fields (numbers greater than 0)."));
    line(r, fields);
    box.querySelector("[data-ss-invite]").hidden = mode === "effect";
    var warn = box.querySelector('[data-ss-out="warning"]');
    warn.textContent = r.warning;
    warn.hidden = !r.warning;
    var apply = box.querySelector("[data-ss-apply]");
    if (apply) {
      // the target is the completions: usable participations plus the expected exclusions (W2)
      if (r.ok && r.total !== null) { apply.href = box.dataset.back + "?max_responses=" + r.completions + "#status"; apply.removeAttribute("aria-disabled"); }
      else { apply.removeAttribute("href"); apply.setAttribute("aria-disabled", "true"); }
    }
  }
  // the line of the sample model (_sample.html, app/sample_model.py, S3): "84 verwertbar · 94 Abschlüsse · bei 10 %
  // Ausschlüssen", with the invitations when the survey's study plan has a panel with a response rate
  function line(r, fields) {
    var LOCALE = document.documentElement.lang === "en" ? "en-GB" : "de-DE";
    function set(name, text) { var el = box.querySelector('[data-sm="' + name + '"]'); if (el) el.textContent = text; }
    var has = r.ok && r.total !== null;
    var drop = isNaN(fields.dropout) ? 0 : Math.min(Math.max(fields.dropout, 0), 90);
    set("usable", has ? r.total.toLocaleString(LOCALE) : "–");
    set("completions", has ? r.completions.toLocaleString(LOCALE) + (r.capped ? de_en(" (alle)", " (all)") : "") : "–");
    set("exclusions", Math.round(drop) + de_en(" %", "%"));
    var rate = Number(box.dataset.response || 0), part = box.querySelector("[data-sm-invite]");
    if (part) {
      part.hidden = !(has && rate > 0);
      if (has && rate > 0) {
        set("invite", Math.ceil(r.completions / (rate / 100) - 1e-9).toLocaleString(LOCALE));
        set("response", rate + de_en(" %", "%"));
      }
    }
  }
  // with the role "Bearbeiten" the number also becomes the target of the study plan (UI/UX audit 2026-10-05, V10)
  var applyForm = box.querySelector("[data-ss-apply-form]"), applyLink = box.querySelector("[data-ss-apply]");
  if (applyForm && applyLink) {
    applyLink.addEventListener("click", function (e) {
      var m = /max_responses=(\d+)/.exec(applyLink.getAttribute("href") || "");
      if (!m) return;
      e.preventDefault();
      applyForm.elements.n.value = m[1];
      // the expected exclusions go with it, so that the plan's line names the same numbers as this page (S3)
      // as a whole number, which the plan keeps (12,5 % → 13 %; an empty field leaves the plan's value, F24)
      var excl = v("dropout");
      if (applyForm.elements.excl) applyForm.elements.excl.value = isFinite(excl) ? String(Math.round(excl)) : "";
      applyForm.submit();
    });
  }
  // the curve (PA3): drawn a moment after the last change, it takes some hundred power values
  var figure = document.querySelector("[data-ss-curve]"), timer = null;
  function color(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }
  function esc(text) { return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function draw() {
    if (!figure) return;
    var fields = {};
    FIELDS.forEach(function (name) { fields[name] = v(name); });
    var modeEl = box.querySelector('[data-ss="mode"]');
    fields.mode = modeEl && !modeEl.closest("[data-ss-for]").hidden && modeEl.checked ? "effect" : "n";
    var c = window.QSampleSize.curve(design.value, fields);
    figure.hidden = !c;
    if (!c) return;
    // under 600 px a drawing of its own: narrower and higher, so that its text keeps a readable size when it is
    // shrunk to the screen, and every other mark on the axis (UI/UX audit 2026-10-07, W12)
    var narrow = figure.clientWidth > 0 && figure.clientWidth < 600;
    var W = narrow ? 420 : 900, H = narrow ? 340 : 300, L = narrow ? 50 : 56, R = narrow ? 14 : 20, T = 18, B = 46;
    var fg = color("--fg", "#1c1c1a"), muted = color("--muted", "#6b6b66"), grid = color("--border", "#e3e2dc");
    var accent = color("--accent", "#2957a4"), faint = color("--subtle", "#9a9a93"), bg = color("--surface", "#ffffff");
    var x0 = c.x[0], x1 = c.x[1], step = c.step;
    function x(n) { return L + (W - L - R) * (n - x0) / (x1 - x0); }
    function y(p) { return T + (H - T - B) * (1 - p); }
    var lang = document.documentElement.lang === "en" ? "en-GB" : "de-DE";
    function tick(n) { return c.mode === "effect" ? n.toLocaleString(lang, { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : Math.round(n).toLocaleString(lang); }
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' +
      esc(de_en("Power nach Zahl der Teilnahmen", "Power by number of participations")) + '" font-family="system-ui, sans-serif" font-size="14">' +
      '<rect width="' + W + '" height="' + H + '" fill="' + bg + '"/>';
    [0, 0.2, 0.4, 0.6, 0.8, 1].forEach(function (p) {
      svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(p) + '" y2="' + y(p) + '" stroke="' + grid + '"/>' +
        '<text x="' + (L - 8) + '" y="' + (y(p) + 5) + '" text-anchor="end" fill="' + muted + '">' + Math.round(p * 100) + de_en(" %", "%") + "</text>";
    });
    var ticks = [];
    if (c.mode === "n") for (var at = 0; at <= x1 + 1e-9; at += narrow ? 2 * step : step) ticks.push(at);
    else for (var k = 0; k <= 5; k += narrow ? 2 : 1) ticks.push(x0 + (x1 - x0) * k / 5);
    ticks.forEach(function (at) {
      svg += '<text x="' + x(at) + '" y="' + (H - B + 20) + '" text-anchor="middle" fill="' + muted + '">' + tick(at) + "</text>";
    });
    svg += '<text x="' + ((L + W - R) / 2) + '" y="' + (H - 6) + '" text-anchor="middle" fill="' + fg + '">' +
      esc(c.mode === "effect" ? de_en("Effekt", "Effect") : de_en("verwertbare Teilnahmen", "usable participations")) + "</text>";
    var target = fields.power;
    svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(target) + '" y2="' + y(target) + '" stroke="' + faint + '" stroke-dasharray="5 4"/>';
    c.series.slice().reverse().forEach(function (sr) {
      var d = sr.pts.map(function (p, i) { return (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1); }).join("");
      svg += '<path d="' + d + '" fill="none" stroke="' + (sr.main ? accent : faint) + '" stroke-width="' + (sr.main ? 3 : 1.5) + '"/>';
      // the name where the curve passes 50 %: the curves of the effects lie apart there (at the end they meet at 100 %)
      var last = sr.pts[sr.pts.length - 1];
      // every curve carries its effect, also the thin ones of Cohen's steps, in every design and on a phone (UI/UX
      // audit 2026-10-09, X13: the correlation named only the thick one)
      var half = sr.pts.filter(function (p) { return p[1] >= 0.5 && x(p[0]) < W - R - 90; })[0];
      var size = sr.main ? ' font-weight="600"' : (narrow ? ' font-size="12"' : "");
      svg += half ? '<text x="' + (x(half[0]) + 8) + '" y="' + (y(half[1]) + 4) + '" fill="' + (sr.main ? accent : muted) + '"' +
                    size + ">" + esc(sr.label) + "</text>"
                  : '<text x="' + (x(last[0]) - 4) + '" y="' + (y(last[1]) - 8) + '" text-anchor="end" fill="' + (sr.main ? accent : muted) + '"' +
                    size + ">" + esc(sr.label) + "</text>";
    });
    c.marks.forEach(function (m) {
      svg += '<line x1="' + x(m.x) + '" x2="' + x(m.x) + '" y1="' + y(0) + '" y2="' + y(m.y) + '" stroke="' + accent + '" stroke-dasharray="4 3"/>' +
        '<circle cx="' + x(m.x) + '" cy="' + y(m.y) + '" r="' + (m.minor ? 4 : 6) + '" fill="' + (m.minor ? bg : accent) + '" stroke="' + accent + '" stroke-width="2"/>' +
        '<text x="' + (x(m.x) + 10) + '" y="' + (y(m.y) + 20) + '" fill="' + accent + '"' + (m.minor ? "" : ' font-weight="600"') + ">" + esc(m.label) + "</text>";
    });
    figure.querySelector("[data-ss-curve-plot]").innerHTML = svg + "</svg>";
    // the statement of the figure in words as well: "84 verwertbare Teilnahmen → 80 % Power, 112 → 90 %"
    var said = figure.querySelector("[data-ss-curve-text]");
    if (said) {
      said.textContent = c.marks.map(function (m, i) {
        if (i) return m.label;
        // one sentence per language, "power" lower case in English (glossary; UI/UX audit 2026-10-09, X82)
        var sentence = c.mode === "n" ? de_en("{n} verwertbare Teilnahmen → {p} Power", "{n} usable participations → {p} power")
                                      : de_en("{n} → {p} Power", "{n} → {p} power");
        return sentence.replace("{n}", m.head).replace("{p}", m.power);
      }).join(", ");
      said.hidden = !c.marks.length;
    }
  }
  function later() { clearTimeout(timer); timer = setTimeout(draw, 150); }
  var wasNarrow = null;
  window.addEventListener("resize", function () {
    var now = figure && figure.clientWidth < 600;
    if (figure && now !== wasNarrow) { wasNarrow = now; later(); }
  });
  if (figure) {
    figure.querySelector("[data-ss-curve-png]").addEventListener("click", function () {
      var svg = figure.querySelector("svg");
      if (!svg) return;
      var img = new Image(), scale = 2;
      img.onload = function () {
        var canvas = document.createElement("canvas");
        var box_ = svg.viewBox.baseVal;
        canvas.width = box_.width * scale; canvas.height = box_.height * scale;
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
          if (!blob) return;
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = de_en("powerkurve.png", "power-curve.png");
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        }, "image/png");
      };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
    });
  }
  // the power of the part-worths from the survey's design (PA7, experimental): N for the parameter that needs the
  // most, (z(1 − α/2) + z(power))² · Var / δ² (de Bekker-Grob et al., 2015), α = .05 two-sided
  function choicePower() {
    box.querySelectorAll("[data-ss-choice-power]").forEach(function (part) {
      var data = part.querySelector("[data-ss-choice-data]"), out = part.querySelector("[data-ss-choice-out]");
      if (!data || !out) return;
      var cp = JSON.parse(data.textContent || "{}");
      var delta = Number(String(part.querySelector('[data-ss-choice="delta"]').value).replace(",", "."));
      var pw = Number(part.querySelector('[data-ss-choice="power"]').value);
      if (!(delta > 0) || !cp["var"] || !cp["var"].length) { out.textContent = "–"; return; }
      var z = window.QSampleSize.qnorm(0.975) + window.QSampleSize.qnorm(pw), worst = 0;
      cp["var"].forEach(function (v, i) { if (v > cp["var"][worst]) worst = i; });
      var n = Math.ceil(z * z * cp["var"][worst] / (delta * delta) - 1e-9);
      var lang = document.documentElement.lang === "en" ? "en-GB" : "de-DE";
      var cbc = part.getAttribute("data-ss-choice-power") === "conjoint";
      out.innerHTML = (cbc ? de_en("Nötig, damit jeder Teilnutzen mit dieser Power von 0 verschieden ist: ", "Needed for every part-worth to differ from 0 with this power: ")
                           : de_en("Nötig, damit jeder Wert mit dieser Power von 0 verschieden ist: ", "Needed for every score to differ from 0 with this power: ")) +
        "<strong>" + n.toLocaleString(lang) + "</strong> " + de_en("Personen", "people") +
        ' <span class="hint">' + de_en("(am meisten braucht „", "(the most is needed by “") + esc(cp.labels[worst] || "") + de_en("“)", "”)") + "</span>";
    });
  }
  box.addEventListener("input", choicePower);
  box.addEventListener("change", choicePower);
  choicePower();
  box.addEventListener("input", function () { calc(); later(); });
  box.addEventListener("change", function () { calc(); later(); });
  box.querySelectorAll("[data-ss-set]").forEach(function (b) {
    b.addEventListener("click", function () {
      var kv = b.dataset.ssSet.split("=");
      box.querySelector('[data-ss="' + kv[0] + '"]').value = kv[1];
      calc();
      later();
    });
  });
  calc();
  draw();
})();
