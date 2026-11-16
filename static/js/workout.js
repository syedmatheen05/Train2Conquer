"use strict";

/* Train2Conquer Workout Engine */

// Workout data
let currentWorkout = 0;
let currentScreen = "ready";

// Exercise timera
let exerciseTime = 0;
let exerciseEndAt = null;
let exerciseInterval = null;

// Rest timer
let restTime = 0;
let restEndAt = null;
let restInterval = null;

// Get ready timer
let readyCountdown = 10;
let readyEndAt = null;
let readyInterval = null;

// Pause state
let exercisePaused = false;
let exercisePausedRemaining = 0;

// Prevent multiple submissions
let isFinishing = false;

// Elements
const video = document.getElementById("workout-video");
const videoSource = document.getElementById("video-source");
const workoutNumber = document.getElementById("workout-number");
const exerciseName = document.getElementById("exercise-name");
const reps = document.getElementById("reps");
const previousButton = document.getElementById("previous-button");
const nextButton = document.getElementById("next-button");
const navigationButtons = document.getElementById("navigation-buttons");
const workoutCard = document.querySelector(".workout-card");
const restScreen = document.getElementById("rest-screen");
const restTitle = document.getElementById("rest-title");
const restMessage = document.getElementById("rest-message");
const restTimer = document.getElementById("rest-timer");
const nextExercise = document.getElementById("next-exercise");
const skipRest = document.getElementById("skip-rest");
const addRestTimeButton = document.getElementById("add-rest-time");
const completeWorkoutForm = document.getElementById("completeWorkoutForm");
const workoutProgressFill = document.getElementById("workout-progress-fill");

// Pause button
const pauseExerciseButton = document.getElementById("pause-exercise");

// Basic helpers
function safeNumber(value, fallback = 0) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
        return fallback;
    }
    return number;
}

function formatSeconds(seconds) {
    const value = Math.max(0, Math.ceil(Number(seconds) || 0));
    return `${value} seconds`;
}

// Speech
function speak(text) {
    if (!("speechSynthesis" in window)) {
        return;
    }
    window.speechSynthesis.cancel();
    const speech = new SpeechSynthesisUtterance(String(text));
    speech.rate = 0.9;
    speech.pitch = 1;
    speech.volume = 1;
    window.speechSynthesis.speak(speech);
}

function speakCountdown(number) {
    if (!("speechSynthesis" in window)) {
        return;
    }
    window.speechSynthesis.cancel();
    const speech = new SpeechSynthesisUtterance(String(number));
    speech.rate = 1.2;
    speech.pitch = 1;
    speech.volume = 1;
    window.speechSynthesis.speak(speech);
}

// Timer cleanup
function clearExerciseTimer() {
    if (exerciseInterval !== null) {
        clearInterval(exerciseInterval);
        exerciseInterval = null;
    }
    exerciseEndAt = null;
}

function clearRestTimer() {
    if (restInterval !== null) {
        clearInterval(restInterval);
        restInterval = null;
    }
    restEndAt = null;
}

function clearReadyTimer() {
    if (readyInterval !== null) {
        clearInterval(readyInterval);
        readyInterval = null;
    }
    readyEndAt = null;
}

function stopAllTimers() {
    clearExerciseTimer();
    clearRestTimer();
    clearReadyTimer();
}

function stopEverything() {
    stopAllTimers();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    if (video) {
        video.pause();
    }
    exercisePaused = false;
    exercisePausedRemaining = 0;
    updatePauseButton();
}

// Screen control
//
// scrollWorkoutIntoView() is a safety net for the single-viewport workout
// player: the CSS is sized so the whole card fits one screen on typical
// viewports, but on very short viewports (or if a previous screen left
// the page scrolled down) this guarantees the user always lands back at
// the top of the workout frame instead of having to scroll up manually
// after Next/Previous/skip/finish. "auto" (instant) behavior is used on
// purpose -- a smooth animated scroll on every exercise change would
// itself feel like unwanted motion.
function scrollWorkoutIntoView() {
    if (window.scrollY > 0 || window.pageYOffset > 0) {
        window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
}

function updateWorkoutProgress(index) {
    if (!workoutProgressFill || !workouts.length) {
        return;
    }
    const percent = Math.min(100, Math.round(((index + 1) / workouts.length) * 100));
    workoutProgressFill.style.width = `${percent}%`;
}

function showWorkoutScreen() {
    if (workoutCard) {
        workoutCard.classList.remove("d-none");
    }
    if (navigationButtons) {
        navigationButtons.classList.remove("d-none");
    }
    if (restScreen) {
        restScreen.classList.add("d-none");
    }
    scrollWorkoutIntoView();
}

function showRestScreen() {
    if (workoutCard) {
        workoutCard.classList.add("d-none");
    }
    if (navigationButtons) {
        navigationButtons.classList.add("d-none");
    }
    if (restScreen) {
        restScreen.classList.remove("d-none");
    }
    scrollWorkoutIntoView();
}

// Rest-only controls
function hideRestOnlyControls() {
    if (skipRest) {
        skipRest.classList.add("d-none");
    }
    if (addRestTimeButton) {
        addRestTimeButton.classList.add("d-none");
    }
}

function showRestOnlyControls() {
    if (skipRest) {
        skipRest.classList.remove("d-none");
    }
    if (addRestTimeButton) {
        addRestTimeButton.classList.remove("d-none");
    }
}

// Pause button
function updatePauseButton() {
    if (!pauseExerciseButton) {
        return;
    }
    const workout = workouts[currentWorkout];
    const isTimed = workout && safeNumber(workout.seconds, 0) > 0;
    if (currentScreen !== "workout" ||!isTimed ||exerciseTime <= 0) {
        pauseExerciseButton.classList.add("d-none");
        return;
    }
    pauseExerciseButton.classList.remove("d-none");
    if (exercisePaused) {
        pauseExerciseButton.textContent = "▶ RESUME";
        pauseExerciseButton.setAttribute("aria-label", "Resume workout");
        pauseExerciseButton.classList.add("is-paused");
    } else {
        pauseExerciseButton.textContent = "Ⅱ PAUSE";
        pauseExerciseButton.setAttribute("aria-label", "Pause workout");
        pauseExerciseButton.classList.remove("is-paused");
    }
}

// Exercise timer UI
function updateExerciseTimerUI(seconds) {
    const value = Math.max(0, Math.ceil(Number(seconds) || 0));
    exerciseTime = value;
    if (reps) {
        reps.textContent = `${value} SEC`;
    }
    updatePauseButton();
}

// Rest timer UI
function updateRestTimerUI(seconds) {
    const value = Math.max(0, Math.ceil(Number(seconds) || 0));
    restTime = value;
    if (restTimer) {
        restTimer.textContent = formatSeconds(value);
    }
}

// Video
function stopVideo() {
    if (!video) {
        return;
    }
    video.pause();
    try {
        video.currentTime = 0;
    } catch (error) {
        console.log("Could not reset video position.");
    }
}

function loadExerciseVideo(videoUrl) {
    if (!video || !videoSource) {
        return;
    }
    const wrapper = video.closest(".t2c-video-wrapper");
    if (!videoUrl) {
        stopVideo();
        videoSource.removeAttribute("src");
        video.removeAttribute("src");
        if (wrapper) {
            wrapper.style.display = "none";
        }
        return;
    }
    if (wrapper) {
        wrapper.style.display = "block";
    }
    video.pause();
    videoSource.src = String(videoUrl);
    video.load();
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(function(error) {
            console.log("Autoplay blocked:", error);
        });
    }
}

// Load workout
function loadWorkout(index) {
    const workout = workouts[index];
    if (!workout) {
        return;
    }
    stopAllTimers();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    currentWorkout = index;
    currentScreen = "workout";
    exercisePaused = false;
    exercisePausedRemaining = 0;
    if (workoutNumber) {
        workoutNumber.textContent = `${index + 1}/${workouts.length}`;
    }
    updateWorkoutProgress(index);
    if (exerciseName) {
        exerciseName.textContent = workout.exercise || "Exercise";
    }
    const seconds = safeNumber(workout.seconds, 0);
    if (seconds > 0) {
        updateExerciseTimerUI(seconds);
    } else {
        if (reps) {
            reps.textContent = workout.reps || "";
        }
        updatePauseButton();
    }
    loadExerciseVideo(workout.video);
    if (previousButton) {
        previousButton.disabled = index === 0;
    }
    if (nextButton) {
        if (index === workouts.length - 1) {
            nextButton.textContent = "FINISH ✓";
            nextButton.classList.add("btn-success");
        } else {
            nextButton.textContent = "DONE →";
            nextButton.classList.remove("btn-success");
        }
    }
    showWorkoutScreen();
    if (seconds > 0) {
        startExerciseTimer();
    } else {
        updatePauseButton();
    }
}

// Start exercise timer
function startExerciseTimer() {
    clearExerciseTimer();
    const workout = workouts[currentWorkout];
    if (!workout) {
        return;
    }
    const seconds = safeNumber(workout.seconds, 0);
    if (seconds <= 0) {
        updatePauseButton();
        return;
    }
    exercisePaused = false;
    exercisePausedRemaining = 0;
    exerciseTime = seconds;
    exerciseEndAt = Date.now() + seconds * 1000;
    updateExerciseTimerUI(seconds);
    exerciseInterval = setInterval(updateExerciseTimer, 200);
    updateExerciseTimer();
}

// Update exercise timer
function updateExerciseTimer() {
    if (currentScreen !== "workout") {
        clearExerciseTimer();
        return;
    }
    if (exercisePaused) {
        return;
    }
    if (exerciseEndAt === null) {
        clearExerciseTimer();
        return;
    }
    const remaining = Math.max(0,(exerciseEndAt - Date.now()) / 1000);
    const previousSecond = exerciseTime;
    updateExerciseTimerUI(remaining);
    const currentSecond = exerciseTime;
    if (previousSecond !== currentSecond && currentSecond <= 3 &&currentSecond > 0) {
        speakCountdown(currentSecond);
    }
    if (remaining <= 0) {
        finishExercise();
    }
}

// Pause exercise
function pauseExercise() {
    if (currentScreen !== "workout") {
        return;
    }
    if (exercisePaused) {
        return;
    }
    if (exerciseEndAt === null ||exerciseTime <= 0) {
        return;
    }
    exercisePausedRemaining = Math.max(0,(exerciseEndAt - Date.now()) / 1000);
    exerciseTime = Math.ceil(exercisePausedRemaining);
    exercisePaused = true;
    if (exerciseInterval !== null) {
        clearInterval(exerciseInterval);
        exerciseInterval = null;
    }
    exerciseEndAt = null;
    if (video) {
        video.pause();
    }
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    updateExerciseTimerUI(exercisePausedRemaining);
    updatePauseButton();
}

// Resume exercise
function resumeExercise() {
    if (currentScreen !== "workout") {
        return;
    }
    if (!exercisePaused) {
        return;
    }
    if (exercisePausedRemaining <= 0) {
        exercisePaused = false;
        finishExercise();
        return;
    }
    exerciseEndAt = Date.now() + exercisePausedRemaining * 1000;
    exercisePaused = false;
    exerciseInterval = setInterval(updateExerciseTimer, 200);
    if (video) {
        const playPromise = video.play();
        if (playPromise && typeof playPromise.catch === "function") {
            playPromise.catch(function(error) {
                console.log("Video resume blocked:", error);
            });
        }
    }
    updateExerciseTimer();
    updatePauseButton();
}

// Pause button click
if (pauseExerciseButton) {
    pauseExerciseButton.addEventListener("click", function(event) {
        event.preventDefault();
        if (exercisePaused) {
            resumeExercise();
        } else {
            pauseExercise();
        }
    });
}

// Finish exercise
function finishExercise() {
    clearExerciseTimer();
    exercisePaused = false;
    exercisePausedRemaining = 0;
    updateExerciseTimerUI(0);
    if (video) {
        video.pause();
    }
    updatePauseButton();
    if (currentWorkout === workouts.length - 1) {
        if (reps) {
            reps.textContent = "DONE ✓";
        }
        return;
    }
    startRest();
}

// Start rest
function startRest() {
    stopAllTimers();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    currentScreen = "rest";
    if (restTitle) {
        restTitle.textContent = "REST";
    }
    if (restMessage) {
        restMessage.textContent =
            "Take a short break before your next exercise.";
    }
    const nextIndex = currentWorkout + 1;
    if (nextExercise) {
        if (nextIndex < workouts.length) {
            nextExercise.textContent =
                `Next movement: ${workouts[nextIndex].exercise}`;
        } else {
            nextExercise.textContent =
                "Your workout is almost complete.";
        }
    }
    restTime = safeNumber(workouts[currentWorkout].rest, 0);
    showRestScreen();
    showRestOnlyControls();
    updateRestTimerUI(restTime);
    speak("Take a rest");
    if (restTime <= 0) {
        finishRest();
        return;
    }
    restEndAt = Date.now() + restTime * 1000;
    restInterval = setInterval(updateRestTimer, 200);
    updateRestTimer();
}

// Update rest timer
function updateRestTimer() {
    if (currentScreen !== "rest") {
        clearRestTimer();
        return;
    }
    if (restEndAt === null) {
        clearRestTimer();
        return;
    }
    const remaining = Math.max(
        0,
        (restEndAt - Date.now()) / 1000
    );
    const previousSecond = restTime;
    updateRestTimerUI(remaining);
    const currentSecond = restTime;
    if (previousSecond !== currentSecond &&currentSecond === 7 &&currentWorkout + 1 < workouts.length) {
        speak(`Next exercise is ${workouts[currentWorkout + 1].exercise}`);
    }

    if (previousSecond !== currentSecond &&currentSecond <= 3 &&currentSecond > 0) {
        speakCountdown(currentSecond);
    }

    if (remaining <= 0) {
        finishRest();
    }
}

// Finish rest
function finishRest() {
    clearRestTimer();
    if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
    }
    const nextIndex = currentWorkout + 1;
    if (nextIndex >= workouts.length) {
        return;
    }
    currentWorkout = nextIndex;
    loadWorkout(nextIndex);
    setTimeout(function() {
        if (currentScreen === "workout" && workouts[currentWorkout]) {
            speak(workouts[currentWorkout].exercise);
        }
    }, 300);
}

// Done / Finish button
if (nextButton) {
    nextButton.addEventListener("click", function(event) {
        event.preventDefault();
        if (isFinishing) {
            return;
        }
        if (currentWorkout === workouts.length - 1) {
            isFinishing = true;
            stopEverything();
            currentScreen = "finished";
            if (completeWorkoutForm) {
                if (window.T2CLoader) {
                    window.T2CLoader.show("Saving your workout...");
                }
                nextButton.disabled = true;
                nextButton.textContent = "SAVING...";
                completeWorkoutForm.submit();
            } else {
                console.error("completeWorkoutForm not found.");
                isFinishing = false;
            }
            return;
        }
        if (video) {
            video.pause();
        }
        startRest();
    });
}

// Previous button
if (previousButton) {
    previousButton.addEventListener("click", function(event) {
        event.preventDefault();
        if (currentWorkout <= 0) {
            return;
        }
        stopEverything();
        currentWorkout--;
        loadWorkout(currentWorkout);
        setTimeout(function() {
            if (currentScreen === "workout" && workouts[currentWorkout]) {
                speak(workouts[currentWorkout].exercise);
            }
        }, 300);
    });
}

// Skip rest
if (skipRest) {
    skipRest.addEventListener("click", function(event) {
        event.preventDefault();
        if (currentScreen !== "rest") {
            return;
        }
        clearRestTimer();
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }
        finishRest();
    });
}

// Add rest time (+20 seconds)
if (addRestTimeButton) {
    addRestTimeButton.addEventListener("click", function(event) {
        event.preventDefault();
        if (currentScreen !== "rest") {
            return;
        }
        if (restEndAt === null) {
            return;
        }
        restEndAt = restEndAt + 20 * 1000;
        updateRestTimer();
    });
}

// Get ready
function updateReadyTimerUI(seconds) {
    const value = Math.max(0, Math.ceil(Number(seconds) || 0));
    readyCountdown = value;
    if (restTimer) {
        restTimer.textContent = formatSeconds(value);
    }
}

function startWorkoutCountdown() {
    if (!Array.isArray(workouts) || workouts.length === 0) {
        console.warn("Train2Conquer: no workouts supplied.");
        return;
    }
    stopEverything();
    currentScreen = "ready";
    currentWorkout = 0;
    if (workoutNumber) {
        workoutNumber.textContent = `0/${workouts.length}`;
    }
    if (workoutCard) {
        workoutCard.classList.add("d-none");
    }
    if (navigationButtons) {
        navigationButtons.classList.add("d-none");
    }
    if (restScreen) {
        restScreen.classList.remove("d-none");
    }
    hideRestOnlyControls();
    if (restTitle) {
        restTitle.textContent = "GET READY";
    }
    if (restMessage) {
        restMessage.textContent = "";
    }
    if (nextExercise) {
        nextExercise.textContent =
            `Your first exercise is ${workouts[0].exercise}`;
    }
    readyCountdown = 10;
    updateReadyTimerUI(readyCountdown);
    speak("Get ready");
    readyEndAt = Date.now() + 10 * 1000;
    readyInterval = setInterval(updateReadyTimer, 200);
    updateReadyTimer();
}

// Update get ready timer
function updateReadyTimer() {
    if (currentScreen !== "ready") {
        clearReadyTimer();
        return;
    }
    if (readyEndAt === null) {
        clearReadyTimer();
        return;
    }
    const remaining = Math.max(
        0,
        (readyEndAt - Date.now()) / 1000
    );
    const previousSecond = readyCountdown;
    updateReadyTimerUI(remaining);
    const currentSecond = readyCountdown;
    if (previousSecond !== currentSecond && currentSecond <= 3 && currentSecond > 0) {
        speakCountdown(currentSecond);
    }
    if (remaining <= 0) {
        clearReadyTimer();
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }
        loadWorkout(0);
        setTimeout(function() {
            if (currentScreen === "workout" && workouts[0]) {
                speak(workouts[0].exercise);
            }
        }, 300);
    }
}

// Mobile visibility recovery
document.addEventListener("visibilitychange", function() {
    if (document.visibilityState !== "visible") {
        return;
    }
    if (currentScreen === "workout") {
        if (!exercisePaused) {
            updateExerciseTimer();
        }
    } else if (currentScreen === "rest") {
        updateRestTimer();
    } else if (currentScreen === "ready") {
        updateReadyTimer();
    }
});

// Video error handling
if (video) {
    video.addEventListener("error", function() {
        console.warn(
            "Workout video could not be loaded:",
            video.currentSrc || ""
        );
    });
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.muted = true;
}

// Prevent accidental double submit
if (completeWorkoutForm) {
    completeWorkoutForm.addEventListener("submit", function() {
        if (isFinishing) {
            return;
        }
        isFinishing = true;
    });
}

// Start app
if (Array.isArray(workouts) && workouts.length > 0) {
    startWorkoutCountdown();
} else {
    console.warn("Train2Conquer: workouts array is empty.");
}