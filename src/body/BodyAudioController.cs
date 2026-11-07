using Godot;
using System;
using System.Collections.Generic;

public sealed class BodyAudioController
{
	private readonly AudioStreamPlayer _player = new();
	private readonly Random _rng = new();
	private Func<string> _currentActionProvider = () => "stand";
	private bool _ttsExclusiveMute;

	private static readonly HashSet<string> LoopingSoundAnimations = new()
	{
		"stand_walk", "sleep", "hop", "climb",
	};

	private static readonly Dictionary<string, string[]> SoundMap = new()
	{
		{ "affection",   new[] { "res://asset/Monika/sound/Cuddling.wav", "res://asset/Monika/sound/Giggle.wav", "res://asset/Monika/sound/Happy_Squeals.wav", "res://asset/Monika/sound/Squeal_Happy.wav" } },
		{ "air",         new[] { "res://asset/Monika/sound/Cartoon_Aah.wav", "res://asset/Monika/sound/Cartoon_Jump2.wav" } },
		{ "stand_walk",  new[] { "res://asset/Monika/sound/Cute_Run.wav", "res://asset/Monika/sound/Cartoon_Hop.wav" } },
		{ "sad",         new[] { "res://asset/Monika/sound/Hurt_Aah.wav", "res://asset/Monika/sound/Hurt_Unh.wav", "res://asset/Monika/sound/Sigh_Sleepy.wav" } },
		{ "read",        new[] { "res://asset/Monika/sound/Nyaa_Soft.wav" } },
		{ "piano",       new[] { "res://asset/Monika/sound/Giggle_Short.wav", "res://asset/Monika/sound/Giggle.wav" } },
		{ "food",        new[] { "res://asset/Monika/sound/Cartoon_Plop.wav", "res://asset/Monika/sound/Cartoon_Pop.wav" } },
		{ "clone",       new[] { "res://asset/Monika/sound/Cartoon_Pop.wav", "res://asset/Monika/sound/Happy_Squeals.wav" } },
		{ "divine",      new[] { "res://asset/Monika/sound/Cartoon_Pop.wav", "res://asset/Monika/sound/Happy_Squeals.wav" } },
		{ "stand",       new[] { "res://asset/Monika/sound/Nyaa_Quick.wav", "res://asset/Monika/sound/Nyaa_Soft.wav" } },
		{ "sit_down",    new[] { "res://asset/Monika/sound/Cartoon_Plop.wav" } },
		{ "sit_up",      new[] { "res://asset/Monika/sound/Cartoon_Plop.wav", "res://asset/Monika/sound/Nyaa_Quick.wav" } },
		{ "sit",         new[] { "res://asset/Monika/sound/Cartoon_Plop.wav", "res://asset/Monika/sound/Nyaa_Soft.wav" } },
		{ "climb",       new[] { "res://asset/Monika/sound/Cute_Run.wav" } },
		{ "climb_top",   new[] { "res://asset/Monika/sound/Cute_Run.wav", "res://asset/Monika/sound/Cartoon_Impact_F.wav" } },
		{ "hop",         new[] { "res://asset/Monika/sound/Cartoon_Hop.wav", "res://asset/Monika/sound/Cartoon_Jump2.wav" } },
		{ "jump",        new[] { "res://asset/Monika/sound/Cartoon_Jump2.wav", "res://asset/Monika/sound/Cartoon_Aah.wav" } },
		{ "sleep",       new[] { "res://asset/Monika/sound/Sigh_Sleepy.wav", "res://asset/Monika/sound/Cuddling.wav" } },
		{ "air_swing_l", new[] { "res://asset/Monika/sound/Cartoon_Aah.wav", "res://asset/Monika/sound/Squeal_Happy.wav" } },
		{ "air_swing_r", new[] { "res://asset/Monika/sound/Cartoon_Aah.wav", "res://asset/Monika/sound/Squeal_Happy.wav" } },
	};

	public void Attach(Node owner, Func<string> currentActionProvider)
	{
		_currentActionProvider = currentActionProvider ?? _currentActionProvider;
		owner.AddChild(_player);
		_player.Finished += OnFinished;
	}

	public void PlayForAnimation(string animation)
	{
		if (_ttsExclusiveMute)
			return;
		if (!SoundMap.TryGetValue(animation, out var sounds))
			return;

		string path = sounds[_rng.Next(sounds.Length)];
		var stream = GD.Load<AudioStream>(path);
		if (stream == null)
		{
			GD.PrintErr($"[Sound] Khong tai duoc: {path}");
			return;
		}

		_player.Stream = stream;
		_player.Play();
	}

	public void SetTtsExclusiveMute(bool muted)
	{
		_ttsExclusiveMute = muted;
		if (muted && _player.Playing)
			_player.Stop();
	}

	private void OnFinished()
	{
		if (_ttsExclusiveMute)
			return;
		string currentAction = _currentActionProvider?.Invoke() ?? "stand";
		if (LoopingSoundAnimations.Contains(currentAction))
			PlayForAnimation(currentAction);
	}
}
